"""The phone tree: a menu the caller works with the keypad, and what it says in answer.

The tree is data (tree.yaml, see the node types described there); this module loads and checks it, and `TreeRunner` walks it through one call. Everything the tree says about
a member comes from `Fixture.lookup`, the same lookup the conversational line answers from, so a question has the same answer on either. Keys arrive as Vonage's
`websocket:dtmf` events (see server.py); speech is ignored. A key pressed while a menu or an entry prompt is playing cuts the prompt short, as on a real line.
"""
from __future__ import annotations

import asyncio
import datetime
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

import yaml
from websockets.exceptions import ConnectionClosed

from .audio import FRAME_BYTES, FRAME_SECONDS, hold_music_pcm
from .coverage import Fixture
from .prompts import Prompts
from .speech_text import date_words, digits_spoken, dollars, months, number_words, numbers_in_words, percent, spoken_question

TREE_PATH = Path(__file__).with_name("tree.yaml")
KEYS = frozenset("0123456789*#")
NODE_TYPES = ("say", "menu", "collect", "action", "form", "hold", "transfer", "hangup")
FORM_PAGE_SIZE = 7                         # questions read out on one page of a form menu (keys 1 to 7; 9 is "more", * is back)


class TreeError(ValueError):
    """The tree file is not usable."""


class TreeCall(Protocol):
    """The call as the tree sees it: what the caller sends, in order, and where the line's audio goes."""

    async def receive(self) -> Optional[Tuple[str, Any]]:
        """("dtmf", "5") for a key, ("audio", bytes) for the caller's audio, None when the caller has gone."""

    async def send_audio(self, data: bytes) -> None: ...


# ---- the actions: what the tree says from the lookup ------------------------------------------------------------------------------------

@dataclass
class Session:
    fields: Dict[str, str] = field(default_factory=dict)
    lookup: Dict[str, Any] = field(default_factory=dict)
    identity: Dict[str, str] = field(default_factory=dict)         # what found the member (empty for a plan found by group number alone)
    attempts: int = 0                                  # failed verifications in a row


@dataclass
class ActionContext:
    session: Session
    fixture: Fixture
    max_failures: int
    args: Dict[str, Any]                               # the node's `args`
    reference: str                                     # this call's reference number


def _who(result: Dict[str, Any]) -> str:
    member = result.get("member") or {}
    return f"{member.get('first_name', '')} {member.get('last_name', '')}".strip() or "the member"


def _period(period: str) -> str:
    return numbers_in_words(period)


def _failed(ctx: ActionContext) -> str:
    ctx.session.attempts += 1
    return "giveup" if ctx.session.attempts >= ctx.max_failures else "unverified"


def _verify_member(ctx: ActionContext) -> Tuple[str, Optional[str]]:
    digits, dob = ctx.session.fields["member"], ctx.session.fields["dob"]
    identity = {"member_id": "M" + digits, "date_of_birth": f"{dob[4:]}-{dob[:2]}-{dob[2:4]}"}
    result = ctx.fixture.lookup(**identity)
    if not result.get("verified"):
        return _failed(ctx), None
    ctx.session.attempts = 0
    ctx.session.lookup, ctx.session.identity = result, identity
    eligibility = result["eligibility"]
    if eligibility["status"] == "terminated":
        return "terminated", f"Coverage for {_who(result)} ended on {date_words(eligibility['termination_date'])}. No benefit information is available."
    return "verified", f"Thank you. I found {_who(result)}. Coverage is active."


def _verify_group(ctx: ActionContext) -> Tuple[str, Optional[str]]:
    result = ctx.fixture.lookup(group_number="G" + ctx.session.fields["group"])
    if not result.get("plan_level_only"):
        return _failed(ctx), None
    ctx.session.attempts = 0
    ctx.session.lookup, ctx.session.identity = result, {}
    return "found", f"Thank you. I found the plan {result['plan']['name']}. Member details are not available without a member."


def _say_eligibility(ctx: ActionContext) -> Tuple[str, Optional[str]]:
    result = ctx.session.lookup
    if "eligibility" not in result:
        return "done", f"Eligibility needs a member. The plan is {result['plan']['name']}."
    return "done", f"Coverage for {_who(result)} is active, effective {date_words(result['eligibility']['effective_date'])}. The plan is {result['plan']['name']}."


def _say_maximum(ctx: ActionContext) -> Tuple[str, Optional[str]]:
    plan = ctx.session.lookup["plan"]
    if "annual_maximum_remaining" not in plan:                      # a plan found by group number: no member, so nothing used
        return "done", f"The annual maximum is {dollars(plan['annual_maximum'])}. The deductible is {dollars(plan['deductible'])}."
    deductible = (f"The deductible is {dollars(plan['deductible'])}, and it has been met." if plan["deductible_remaining"] == 0
                  else f"The deductible is {dollars(plan['deductible'])}, with {dollars(plan['deductible_remaining'])} still to be met.")
    return "done", f"The annual maximum is {dollars(plan['annual_maximum'])}, with {dollars(plan['annual_maximum_remaining'])} remaining. {deductible}"


_CATEGORY_SPOKEN = {"preventive": "Preventive care", "basic": "Basic services", "major": "Major services", "orthodontics": "Orthodontics"}


def _say_percentages(ctx: ActionContext) -> Tuple[str, Optional[str]]:
    sentences = []
    for category, benefit in ctx.session.lookup["benefits"].items():
        name = _CATEGORY_SPOKEN.get(category, category.capitalize())
        sentences.append(f"{name} is covered at {percent(benefit['coverage_percent'])}." if benefit["covered"] else f"{name} is not covered by this plan.")
    plan = ctx.session.lookup["plan"]
    if "orthodontic_lifetime_maximum" in plan:
        sentences.append(f"The orthodontic lifetime maximum is {dollars(plan['orthodontic_lifetime_maximum'])}.")
    return "done", " ".join(sentences)


def _say_waiting(ctx: ActionContext) -> Tuple[str, Optional[str]]:
    sentences = []
    for category, benefit in ctx.session.lookup["benefits"].items():
        if benefit["covered"]:
            wait = benefit.get("waiting_period_months")
            sentences.append(f"{_CATEGORY_SPOKEN.get(category, category.capitalize())}: " + (f"{months(wait)}." if wait else "no waiting period."))
    return "done", "Waiting periods. " + " ".join(sentences)


def _say_services(ctx: ActionContext) -> Tuple[str, Optional[str]]:
    """The services in one category (`args.category`), each with its frequency limit and, for a member, how much of it is used."""
    category = ctx.args["category"]
    name = _CATEGORY_SPOKEN.get(category, category.capitalize())
    benefit = ctx.session.lookup["benefits"].get(category) or {"covered": False}
    if not benefit["covered"]:
        return "done", f"The plan has no benefit for {category}."
    parts = []
    for service in benefit["services"]:
        limit = service.get("frequency_limit")
        if not limit:
            parts.append(f"{service['description']}: no frequency limit.")
            continue
        text = f"{service['description']}: {number_words(limit['times'])} per {_period(limit['period'])}"
        if "times_used" in service:
            text += f", {number_words(service['times_used'])} used and {number_words(service['times_remaining'])} remaining"
        parts.append(text + ".")
    return "done", f"{name} are covered at {percent(benefit['coverage_percent'])}. " + " ".join(parts)


def _say_procedure(ctx: ActionContext) -> Tuple[str, Optional[str]]:
    if not ctx.session.identity:
        return "done", "A procedure check needs a member."
    digits = ctx.session.fields["code"]
    procedure = ctx.fixture.lookup(**ctx.session.identity, procedure_code="D" + digits).get("procedure") or {"found": False}
    if not procedure["found"]:
        return "done", f"Procedure code D {digits_spoken(digits)} is not on file."
    if "coverage_percent" not in procedure:
        return "done", f"{procedure['description']} is not covered now: {procedure['reason']}."
    parts = [f"{procedure['description']} is covered at {percent(procedure['coverage_percent'])}.",
             "The deductible applies." if procedure["deductible_applies"] else "The deductible does not apply."]
    limit = procedure.get("frequency_limit")
    if limit:
        parts.append(f"The limit is {number_words(limit['times'])} per {_period(limit['period'])}, with {number_words(limit['times_used'])} used "
                     f"and {number_words(limit['times_remaining'])} remaining.")
    parts.append("It is covered now." if procedure["covered_now"] else f"It is not covered now: {procedure['reason']}.")
    return "done", " ".join(parts)


def _say_reference(ctx: ActionContext) -> Tuple[str, Optional[str]]:
    employee = ctx.fixture.standing_answers.get("employee_id")
    return "done", ("This is the automated provider line. " + (f"The I D is {digits_spoken(employee)}. " if employee else "") +
                    (f"The reference number for this call is {digits_spoken(ctx.reference)}." if ctx.reference else "There is no reference number for this call."))


Action = Callable[[ActionContext], Tuple[str, Optional[str]]]
ACTIONS: Dict[str, Tuple[Action, frozenset]] = {
    "verify_member": (_verify_member, frozenset({"verified", "terminated", "unverified", "giveup"})),
    "verify_group": (_verify_group, frozenset({"found", "unverified", "giveup"})),
    "say_eligibility": (_say_eligibility, frozenset({"done"})),
    "say_maximum": (_say_maximum, frozenset({"done"})),
    "say_percentages": (_say_percentages, frozenset({"done"})),
    "say_waiting": (_say_waiting, frozenset({"done"})),
    "say_services": (_say_services, frozenset({"done"})),
    "say_procedure": (_say_procedure, frozenset({"done"})),
    "say_reference": (_say_reference, frozenset({"done"})),
}


# ---- the form menus: the practice's verification-form questions, by topic ----------------------------------------------------------------

def form_pages(entries: List[Dict[str, str]]) -> List[List[Dict[str, str]]]:
    return [entries[i:i + FORM_PAGE_SIZE] for i in range(0, len(entries), FORM_PAGE_SIZE)]


def form_prompt(node: Dict[str, Any], pages: List[List[Dict[str, str]]], page: int) -> str:
    """What a form menu says for one page: its introduction, each question with its key, then the keys for more and for back."""
    parts = [str(node["say"]).strip()]
    parts += [f"For {spoken_question(entry['question'])}, press {number}." for number, entry in enumerate(pages[page], start=1)]
    if len(pages) > 1:
        parts.append("For more questions, press 9." if page < len(pages) - 1 else "To hear the first questions again, press 9.")
    parts.append("To go back, press star.")
    return " ".join(parts)


def _real_date(mmddyyyy: str) -> bool:
    try:
        datetime.date(int(mmddyyyy[4:]), int(mmddyyyy[:2]), int(mmddyyyy[2:4]))
    except ValueError:
        return False
    return True


# ---- the tree file ----------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class PhoneTree:
    settings: Dict[str, Any]
    start: str
    nodes: Dict[str, Dict[str, Any]]

    @classmethod
    def load(cls, path: Path = TREE_PATH) -> "PhoneTree":
        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "PhoneTree":
        nodes = raw.get("nodes") or {}
        settings = {"entry_timeout_seconds": 8, "max_failures": 3, "hold_seconds": 8, "giveup": None, **(raw.get("settings") or {})}
        start = raw.get("start")
        if not nodes or start not in nodes:
            raise TreeError("the tree needs nodes and a `start` that names one")
        if settings["max_failures"] < 1 or settings["entry_timeout_seconds"] <= 0 or settings["hold_seconds"] < 0:
            raise TreeError("settings: max_failures must be at least 1, entry_timeout_seconds above 0 and hold_seconds not negative")

        def target(where: str, name: Any) -> str:
            if name not in nodes:
                raise TreeError(f"{where}: no node called {name!r}")
            return name

        if settings["giveup"] is not None:
            target("settings giveup", settings["giveup"])
        for name, node in nodes.items():
            kind = node.get("type")
            if kind not in NODE_TYPES:
                raise TreeError(f"node {name}: type must be one of {', '.join(NODE_TYPES)}")
            for key in ("say", "invalid", "timeout"):
                if "{" in str(node.get(key) or "") or "}" in str(node.get(key) or ""):
                    raise TreeError(f"node {name}: {key} may not contain braces")
            if kind in ("say", "menu", "collect", "transfer", "form") and not str(node.get("say") or "").strip():
                raise TreeError(f"node {name}: needs `say`")
            if kind in ("say", "collect", "hold"):
                target(f"node {name} next", node.get("next"))
            if kind in ("menu", "collect"):
                if settings["giveup"] is None and not node.get("giveup"):
                    raise TreeError(f"node {name}: needs `giveup` (or a giveup in settings)")
                if node.get("giveup"):
                    target(f"node {name} giveup", node["giveup"])
            if kind == "menu":
                options = node.get("options") or {}
                if not options:
                    raise TreeError(f"node {name}: a menu needs options")
                for key, destination in options.items():
                    if str(key) not in KEYS:
                        raise TreeError(f"node {name}: {key!r} is not a key (0-9, * or #)")
                    target(f"node {name} option {key}", destination)
            if kind == "collect":
                if not str(node.get("field") or ""):
                    raise TreeError(f"node {name}: needs `field`")
                if not isinstance(node.get("length"), int) or not 1 <= node["length"] <= 20:
                    raise TreeError(f"node {name}: length must be a whole number from 1 to 20")
                if node.get("check") not in (None, "date"):
                    raise TreeError(f"node {name}: check must be `date` or left out")
                if node.get("check") == "date" and node["length"] != 8:
                    raise TreeError(f"node {name}: a date is eight digits")
                if node.get("star_to"):
                    target(f"node {name} star_to", node["star_to"])
            if kind == "form":
                if not str(node.get("topic") or ""):
                    raise TreeError(f"node {name}: needs `topic`")
                target(f"node {name} back", node.get("back"))
            if kind == "action":
                if node.get("action") not in ACTIONS:
                    raise TreeError(f"node {name}: action must be one of {', '.join(ACTIONS)}")
                routes = node.get("routes") or {}
                if set(routes) != ACTIONS[node["action"]][1]:
                    raise TreeError(f"node {name}: routes must cover exactly {sorted(ACTIONS[node['action']][1])}")
                for outcome, destination in routes.items():
                    target(f"node {name} route {outcome}", destination)

        tree = cls(settings=settings, start=start, nodes=nodes)
        unreachable = sorted(set(nodes) - tree._reachable())
        if unreachable:
            raise TreeError("not reachable from the start: " + ", ".join(unreachable))
        return tree

    def _reachable(self) -> set:
        seen, todo = set(), [self.start]
        while todo:
            name = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            node = self.nodes[name]
            todo += [str(v) for v in (node.get("next"), node.get("star_to"), node.get("giveup"), node.get("back"), self.settings["giveup"]) if v]
            todo += [str(v) for v in (node.get("options") or {}).values()] + [str(v) for v in (node.get("routes") or {}).values()]
        return seen

    def check_against(self, fixture: Fixture) -> None:
        """The tree and the fixture must agree: every form menu's topic has questions, and every topic the fixture's questions have is reachable from a form menu."""
        topics = fixture.benefit_topics()
        used = {str(node["topic"]) for node in self.nodes.values() if node["type"] == "form"}
        missing = sorted(used - set(topics))
        if missing:
            raise TreeError("form menus name topics the fixture has no questions for: " + ", ".join(missing))
        unreachable = sorted(set(topics) - used)
        if unreachable:
            raise TreeError("fixture questions no form menu reaches: " + ", ".join(unreachable))

    def texts(self, fixture: Optional[Fixture] = None) -> List[str]:
        """Every fixed line the tree can say, so the audio can be prepared ahead of calls: the menus and, given the fixture, every page of every form menu and every answer on
        them. What an action says is built from a member's coverage and is prepared the first time it is spoken."""
        out: List[str] = []
        for node in self.nodes.values():
            out += [str(node[key]).strip() for key in ("say", "invalid", "timeout") if node.get(key) and node["type"] != "form"]
            if node["type"] == "form" and fixture is not None:
                entries = fixture.benefit_topics()[str(node["topic"])]
                pages = form_pages(entries)
                out += [form_prompt(node, pages, page) for page in range(len(pages))] + [entry["answer"] for entry in entries]
                out += [str(node[key]).strip() for key in ("invalid", "timeout") if node.get(key)]
        return list(dict.fromkeys(out))


# ---- one call ---------------------------------------------------------------------------------------------------------------------------

@dataclass
class TreeResult:
    outcome: str                               # "hangup" (the tree ended the call), "transfer" (on to the representative), "caller_gone"
    events: List[Dict[str, Any]]
    duration_seconds: float = 0.0


class _End(Exception):
    def __init__(self, outcome: str):
        self.outcome = outcome


class TreeRunner:
    def __init__(self, tree: PhoneTree, fixture: Fixture, prompts: Prompts, call: TreeCall, *, reference: str = "", ring_audio: bytes = b"", answer_delay_seconds: float = 0.0,
                 pace: float = FRAME_SECONDS, clock: Callable[[], float] = time.monotonic):
        self._tree, self._fixture, self._prompts, self._call, self._reference = tree, fixture, prompts, call, reference
        self._ring_audio, self._answer_delay, self._pace, self._clock = ring_audio, answer_delay_seconds, pace, clock
        self._keys: "asyncio.Queue[Optional[str]]" = asyncio.Queue()
        self._session = Session()
        self._events: List[Dict[str, Any]] = []
        self._started = clock()
        self._node = ""

    @property
    def events(self) -> List[Dict[str, Any]]:
        return self._events

    def _event(self, **fields: Any) -> None:
        self._events.append({"at": round(self._clock() - self._started, 1), "node": self._node, **fields})

    # ---- keys and sound ------------------------------------------------------

    async def _pump(self) -> None:
        while True:
            item = await self._call.receive()
            if item is None:
                self._keys.put_nowait(None)
                return
            if item[0] == "dtmf" and item[1] in KEYS:
                self._keys.put_nowait(item[1])

    def _pending_key(self) -> Optional[str]:
        """A key already waiting, without waiting for one. The caller having gone ends the call."""
        try:
            item = self._keys.get_nowait()
        except asyncio.QueueEmpty:
            return None
        if item is None:
            raise _End("caller_gone")
        return item

    async def _wait_key(self, timeout: float) -> Optional[str]:
        try:
            item = await asyncio.wait_for(self._keys.get(), timeout)
        except asyncio.TimeoutError:
            return None
        if item is None:
            raise _End("caller_gone")
        return item

    async def _play(self, pcm: bytes, barge_in: bool) -> Optional[str]:
        """Play the audio at the pace it is heard. A key pressed meanwhile stops it and is returned when `barge_in`; otherwise keys pressed meanwhile are dropped."""
        for start in range(0, len(pcm), FRAME_BYTES):
            key = self._pending_key()
            if key is not None and barge_in:
                return key
            try:
                await self._call.send_audio(pcm[start:start + FRAME_BYTES])
            except ConnectionClosed:                              # the caller hung up while the line was speaking
                raise _End("caller_gone") from None
            await asyncio.sleep(self._pace)
        if not barge_in:
            while self._pending_key() is not None:
                pass
        return None

    async def _say(self, text: str, barge_in: bool = False) -> Optional[str]:
        return await self._play(await self._prompts.audio(text.strip()), barge_in)

    # ---- the node types ------------------------------------------------------

    async def _failure(self, node: Dict[str, Any], failures: int, kind: str, **detail: Any) -> Optional[str]:
        """Note a wrong or missing entry and say so. The destination when it has happened too often, else None."""
        self._event(failure=kind, **detail)
        if failures >= self._tree.settings["max_failures"]:
            return node.get("giveup") or self._tree.settings["giveup"]
        line = node.get(kind) or {"invalid": "That is not a valid entry.", "timeout": "We did not receive your entry."}[kind]
        await self._say(line)
        return None

    async def _menu(self, node: Dict[str, Any]) -> str:
        failures = 0
        while True:
            key = await self._say(node["say"], barge_in=True)
            if key is None:
                key = await self._wait_key(self._tree.settings["entry_timeout_seconds"])
            if key is not None and str(key) in {str(k) for k in node["options"]}:
                self._event(key=key)
                return next(str(v) for k, v in node["options"].items() if str(k) == key)
            failures += 1
            gave_up = await self._failure(node, failures, "timeout" if key is None else "invalid", **({} if key is None else {"key": key}))
            if gave_up:
                return gave_up

    async def _collect(self, node: Dict[str, Any]) -> str:
        failures = 0
        while True:
            digits, key, failed = "", await self._say(node["say"], barge_in=True), None
            while failed is None:
                if key is None:
                    key = await self._wait_key(self._tree.settings["entry_timeout_seconds"])
                if key is None:
                    failed = "timeout"
                elif key == "*":
                    if node.get("star_to"):
                        self._event(key="*")
                        return str(node["star_to"])
                    digits = ""
                elif key == "#":
                    if len(digits) != node["length"]:
                        failed = "invalid"
                else:
                    digits += key
                if failed is None and (len(digits) == node["length"]):
                    if node.get("check") == "date" and not _real_date(digits):
                        failed = "invalid"
                    else:
                        self._session.fields[node["field"]] = digits
                        self._event(entered=node["field"], digits=digits)
                        return str(node["next"])
                key = None
            failures += 1
            gave_up = await self._failure(node, failures, failed, entered=digits)
            if gave_up:
                return gave_up

    async def _form(self, node: Dict[str, Any]) -> str:
        """A form menu: the questions of one topic, a page at a time. A question's key says its answer and stays on the page; 9 turns the page, * goes back."""
        pages = form_pages(self._fixture.benefit_topics()[str(node["topic"])])
        page = failures = 0
        while True:
            key = await self._say(form_prompt(node, pages, page), barge_in=True)
            if key is None:
                key = await self._wait_key(self._tree.settings["entry_timeout_seconds"])
            if key == "*":
                self._event(key=key)
                return str(node["back"])
            if key == "9" and len(pages) > 1:
                self._event(key=key)
                page, failures = (page + 1) % len(pages), 0
                continue
            if key is not None and key.isdigit() and 1 <= int(key) <= len(pages[page]):
                entry = pages[page][int(key) - 1]
                self._event(key=key, question=entry["question"])
                failures = 0
                await self._say(entry["answer"])
                continue
            failures += 1
            gave_up = await self._failure(node, failures, "timeout" if key is None else "invalid", **({} if key is None else {"key": key}))
            if gave_up:
                return gave_up

    async def _action(self, node: Dict[str, Any]) -> str:
        function, _outcomes = ACTIONS[node["action"]]
        outcome, text = function(ActionContext(self._session, self._fixture, self._tree.settings["max_failures"], node.get("args") or {}, self._reference))
        self._event(action=node["action"], outcome=outcome)
        if text:
            await self._say(text)
        return str(node["routes"][outcome])

    async def _hold(self, node: Dict[str, Any]) -> str:
        seconds = node.get("seconds", self._tree.settings["hold_seconds"])
        self._event(hold_seconds=seconds)
        await self._play(hold_music_pcm(seconds), barge_in=False)
        return str(node["next"])

    async def run(self) -> TreeResult:
        pump = asyncio.create_task(self._pump())
        outcome = "hangup"
        try:
            if self._ring_audio:
                await self._play(self._ring_audio, barge_in=False)
            if self._answer_delay:
                await asyncio.sleep(self._answer_delay)
            name = self._tree.start
            while True:
                self._node, node = name, self._tree.nodes[name]
                self._event(visit=True)
                kind = node["type"]
                if kind == "say":
                    await self._say(node["say"])
                    name = str(node["next"])
                elif kind == "menu":
                    name = await self._menu(node)
                elif kind == "collect":
                    name = await self._collect(node)
                elif kind == "action":
                    name = await self._action(node)
                elif kind == "form":
                    name = await self._form(node)
                elif kind == "hold":
                    name = await self._hold(node)
                elif kind == "transfer":
                    await self._say(node["say"])
                    outcome = "transfer"
                    break
                else:                                           # hangup
                    if node.get("say"):
                        await self._say(node["say"])
                    break
        except _End as end:
            outcome = end.outcome
        finally:
            pump.cancel()
        return TreeResult(outcome=outcome, events=self._events, duration_seconds=self._clock() - self._started)
