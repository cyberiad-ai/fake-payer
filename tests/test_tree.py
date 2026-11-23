"""The phone tree: the file is sound, and walking it by keypad gives the right words and the right ending. The call is a fake that lets a key through once the line has said
something that contains a given phrase, which is what a caller does (listens, then presses); the prompts are the text itself as bytes, one frame each."""
import asyncio
import copy
from typing import Any, List, Optional, Tuple

import pytest

from fake_payer.audio import FRAME_BYTES, hold_music_pcm
from fake_payer.coverage import Fixture
from fake_payer.tree import ACTIONS, PhoneTree, TreeError, TreeRunner

FIXTURE = Fixture.load()
REAL_TREE = PhoneTree.load()


class FakePrompts:
    """The audio for a line is the line's own text (under one frame, so one send), which lets a test read what was said."""

    async def audio(self, text: str) -> bytes:
        return text.encode()


class ScriptedCall:
    """`script` is a list of (phrase, key): the key is delivered once the line has said something containing the phrase after the previous key (an empty phrase delivers at
    once). A key of None delivers nothing for that step, it only waits for the phrase. When the script is used up the caller stays silent (or, with `hang_up_after`, goes away)."""

    def __init__(self, script: List[Tuple[str, Optional[str]]], hang_up_after: bool = False):
        self.script = list(script)
        self.said: List[str] = []
        self.hold_frames = 0
        self._arrived = asyncio.Event()
        self._hang_up_after = hang_up_after
        self._mark = 0

    async def send_audio(self, data: bytes) -> None:
        if not data[:1].isalpha():                                     # the hold music, not a line of text
            self.hold_frames += 1
            return
        self.said.append(data.decode(errors="replace"))
        self._arrived.set()

    async def receive(self) -> Optional[Tuple[str, Any]]:
        while True:
            if not self.script:
                if self._hang_up_after:
                    return None
                await asyncio.Event().wait()
            phrase, key = self.script.pop(0)
            while phrase and not any(phrase in line for line in self.said[self._mark:]):
                self._arrived.clear()
                await self._arrived.wait()
            self._mark = len(self.said)
            if key is not None:
                return "dtmf", key


def enter(prompt, digits, then=None):
    return [(prompt, digits[0])] + [("", d) for d in digits[1:]] + ([(then, None)] if then else [])


async def walk(script, tree=REAL_TREE, hang_up_after=False, seconds=5.0):
    call = ScriptedCall(script, hang_up_after=hang_up_after)
    runner = TreeRunner(tree, FIXTURE, FakePrompts(), call, pace=0)
    result = await asyncio.wait_for(runner.run(), seconds)
    return result, call


def small(**changes) -> PhoneTree:
    """The real tree with quick timeouts, and any node changed."""
    raw = copy.deepcopy({"settings": REAL_TREE.settings, "start": REAL_TREE.start, "nodes": REAL_TREE.nodes})
    raw["settings"].update(entry_timeout_seconds=0.05, hold_seconds=0.02)
    for name, node in changes.items():
        raw["nodes"][name] = node
    return PhoneTree.from_dict(raw)


# ---- the file ------------------------------------------------------------------------------------------------------------------------

def test_the_real_tree_loads_and_every_node_can_be_reached():
    assert REAL_TREE.start == "greeting" and "main" in REAL_TREE.nodes
    assert REAL_TREE.texts()                                  # there is something to prepare


def test_the_tree_has_the_dead_ends_it_is_meant_to_have():
    kinds = {name: node["type"] for name, node in REAL_TREE.nodes.items()}
    assert kinds["claims"] == "hangup"                                              # a dead end that ends the call
    assert REAL_TREE.nodes["enroll_new"]["next"] == "enrollment"                    # a loop with no way forward
    assert REAL_TREE.nodes["supervisor_gone"]["type"] == "hangup" and kinds["supervisor_hold"] == "hold"
    assert kinds["representative_transfer"] == "transfer"


def broken(**changes):
    raw = copy.deepcopy({"settings": REAL_TREE.settings, "start": REAL_TREE.start, "nodes": REAL_TREE.nodes})
    for name, node in changes.items():
        if node is None:
            del raw["nodes"][name]
        else:
            raw["nodes"][name] = node
    return raw


@pytest.mark.parametrize("raw,message", [
    ({"nodes": {}, "start": "a"}, "needs nodes"),
    (broken(main={"type": "menu", "say": "x", "options": {"1": "nowhere"}}), "no node called 'nowhere'"),
    (broken(main={"type": "menu", "say": "x", "options": {"A": "main"}}), "not a key"),
    (broken(main={"type": "menu", "say": "x", "options": {}}), "needs options"),
    (broken(main={"type": "menu", "say": "", "options": {"1": "main"}}), "needs `say`"),
    (broken(main={"type": "menu", "say": "use {braces}", "options": {"1": "main"}}), "braces"),
    (broken(main={"type": "door", "say": "x"}), "type must be one of"),
    (broken(claims=None), "no node called 'claims'"),
    (broken(verify={"type": "action", "action": "verify_member", "routes": {"verified": "main"}}), "routes must cover exactly"),
    (broken(verify={"type": "action", "action": "do_magic", "routes": {}}), "action must be one of"),
    (broken(ask_member={"type": "collect", "field": "member", "length": 0, "say": "x", "next": "ask_dob"}), "length"),
    (broken(ask_dob={"type": "collect", "field": "dob", "length": 6, "check": "date", "say": "x", "next": "verify"}), "a date is eight digits"),
    (broken(orphan={"type": "hangup", "say": "x"}), "not reachable from the start: orphan"),
])
def test_a_tree_with_a_fault_is_refused(raw, message):
    with pytest.raises(TreeError, match=message):
        PhoneTree.from_dict(raw)


def test_a_menu_without_anywhere_to_go_when_it_gives_up_is_refused():
    raw = broken()
    raw["settings"]["giveup"] = None
    with pytest.raises(TreeError, match="giveup"):
        PhoneTree.from_dict(raw)


def test_the_actions_declare_the_outcomes_the_file_must_route():
    assert ACTIONS["verify_member"][1] == {"verified", "terminated", "unverified", "giveup"}


# ---- walking it ----------------------------------------------------------------------------------------------------------------------

async def test_the_greeting_and_the_main_menu_are_said_and_a_hang_up_ends_the_call():
    result, call = await walk([("press 1", None)], tree=small(), hang_up_after=True)
    assert result.outcome == "caller_gone"
    assert "Acme Dental Benefits" in call.said[0] and "fictional" in call.said[0] and "recorded" in call.said[0]
    assert call.said[1].startswith("For eligibility and benefits, press 1")


async def test_a_member_is_found_by_keypad_and_the_answers_come_from_the_lookup():
    script = ([("press 1", "1")] + enter("six digit member", "100001") + enter("date of birth", "03141985", "Thank you. I found Pat Sample") +
              [("For eligibility dates", "1"), ("effective January first", None), ("For eligibility dates", "2"), ("annual maximum is", None), ("For eligibility dates", "3"),
               ("Orthodontics is covered", None), ("For eligibility dates", "4"), ("Waiting periods", None), ("For eligibility dates", "*"), ("press 1", None)])
    result, call = await walk(script, tree=small(), hang_up_after=True)
    said = " | ".join(call.said)
    assert result.outcome == "caller_gone"
    assert "Coverage for Pat Sample is active, effective January first, twenty twenty-five. The plan is Acme Gold Dental." in said
    assert "The annual maximum is one thousand five hundred dollars, with one thousand five hundred dollars remaining. The deductible is fifty dollars, with fifty dollars still" in said
    assert "Preventive care is covered at one hundred percent. Basic services is covered at eighty percent. Major services is covered at fifty percent." in said
    assert "Waiting periods. Preventive care: no waiting period. Basic services: six months. Major services: twelve months. Orthodontics: twelve months." in said
    assert "The orthodontic lifetime maximum is one thousand five hundred dollars." in said
    assert [e["digits"] for e in result.events if "digits" in e] == ["100001", "03141985"]
    assert [e["entered"] for e in result.events if "entered" in e] == ["member", "dob"]


async def test_the_figures_are_the_ones_the_conversational_line_would_give():
    plan = FIXTURE.lookup(member_id="M100002", date_of_birth="1990-07-02")["plan"]
    assert plan["annual_maximum_remaining"] == 1320 and plan["deductible_remaining"] == 0
    script = [("press 1", "1")] + enter("six digit member", "100002") + enter("date of birth", "07021990", "I found Robin Example") + [("For eligibility dates", "2"), ("annual", None)]
    result, call = await walk(script, tree=small(), hang_up_after=True)
    assert any("with one thousand three hundred twenty dollars remaining. The deductible is fifty dollars, and it has been met." in line for line in call.said)


async def test_a_terminated_member_gets_the_end_date_and_no_benefits():
    script = ([("press 1", "1")] + enter("six digit member", "100003") +
              enter("date of birth", "11301978", "Coverage for Casey Placeholder ended on June thirtieth, twenty twenty-six") + [("To check another member", "3")])
    result, call = await walk(script, tree=small())
    assert result.outcome == "hangup" and call.said[-1] == "Thank you for calling. Goodbye."
    assert not any("annual maximum" in line for line in call.said)


async def test_a_wrong_date_of_birth_is_refused_and_after_three_tries_the_call_ends():
    def attempt(then=None):
        return enter("six digit member", "100001") + enter("date of birth", "01011999", then)

    script = [("press 1", "1")] + attempt("We could not verify the member") + attempt("We could not verify the member") + attempt()
    result, call = await walk(script, tree=small())
    assert result.outcome == "hangup" and call.said[-1].startswith("We are unable to verify the member")
    assert sum(1 for line in call.said if line.startswith("We could not verify")) == 2


async def test_an_impossible_date_is_refused_and_asked_for_again():
    script = ([("press 1", "1")] + enter("six digit member", "100001") + enter("date of birth", "13452000", "That is not a valid date") +
              enter("date of birth", "03141985", "I found Pat Sample"))
    result, call = await walk(script, tree=small(), hang_up_after=True)
    assert "That is not a valid date." in call.said and any("I found Pat Sample" in line for line in call.said)
    assert any(e.get("failure") == "invalid" for e in result.events)


async def test_star_during_an_entry_goes_back_to_the_main_menu():
    result, call = await walk([("press 1", "1"), ("six digit member", "*"), ("press 1", None)], tree=small(), hang_up_after=True)
    assert sum(1 for line in call.said if line.startswith("For eligibility and benefits")) == 2


async def test_pressing_a_key_during_a_prompt_cuts_it_off():
    """The main menu is spoken as eight frames and the caller presses 2 while it is playing: the rest of it is not said and 2 (claim status) is taken."""

    class Chunked(FakePrompts):
        async def audio(self, text: str) -> bytes:
            return (text.encode() + b" " * FRAME_BYTES)[:FRAME_BYTES] * 8 if text.startswith("For eligibility and benefits") else text.encode()

    call = ScriptedCall([("For eligibility", "2")])
    result = await asyncio.wait_for(TreeRunner(small(), FIXTURE, Chunked(), call, pace=0).run(), 5)
    menu_frames = [line for line in call.said if line.startswith("For eligibility and benefits")]
    assert len(menu_frames) < 8 and result.outcome == "hangup"
    assert call.said[-1].startswith("Claim status is not available")


# ---- bad paths -----------------------------------------------------------------------------------------------------------------------

async def test_claim_status_is_a_dead_end_that_hangs_up():
    result, call = await walk([("press 1", "2")], tree=small())
    assert result.outcome == "hangup" and call.said[-1] == "Claim status is not available on this line. To check a claim, please use the provider portal. Goodbye."


async def test_an_invalid_key_is_said_so_and_the_menu_starts_again_then_the_call_ends():
    result, call = await walk([("press 1", "8"), ("not a valid option", None), ("press 1", "7"), ("press 1", "6")], tree=small())
    assert result.outcome == "hangup" and call.said[-1] == "We did not receive a valid entry. Goodbye."
    assert sum(1 for line in call.said if line == "That is not a valid option.") == 2
    assert [e["key"] for e in result.events if e.get("failure") == "invalid"] == ["8", "7", "6"]


async def test_silence_is_said_so_and_after_three_the_call_ends():
    result, call = await walk([], tree=small())
    assert result.outcome == "hangup" and call.said[-1] == "We did not receive a valid entry. Goodbye."
    assert call.said.count("We did not receive your entry.") == 2
    assert [e["failure"] for e in result.events if "failure" in e] == ["timeout"] * 3


async def test_a_good_key_after_a_failure_clears_the_count():
    result, call = await walk([("press 1", "8"), ("press 1", "2")], tree=small())
    assert call.said[-1].startswith("Claim status is not available")


async def test_the_fax_option_goes_round_to_the_main_menu():
    result, call = await walk([("press 1", "3"), ("Fax numbers are not available", None), ("press 1", "2")], tree=small())
    assert sum(1 for line in call.said if line.startswith("For eligibility and benefits")) == 2


async def test_provider_enrollment_goes_round_in_a_loop_with_no_way_forward():
    script = [("press 1", "4"), ("To enroll a new provider", "1"), ("credentialing department", None), ("To enroll a new provider", "2"), ("must first be enrolled", None),
              ("To enroll a new provider", "1"), ("credentialing department", None), ("To enroll a new provider", "3"), ("press 1", "2")]
    result, call = await walk(script, tree=small())
    assert sum(1 for line in call.said if line.startswith("To enroll a new provider")) == 4
    assert call.said[-1].startswith("Claim status is not available")


async def test_a_supervisor_never_comes_and_the_call_is_dropped_after_hold_music():
    result, call = await walk([("press 1", "5")], tree=small())
    assert result.outcome == "hangup" and call.hold_frames > 0
    assert call.said[-1] == "All of our supervisors are busy. Please try your call again later. Goodbye."


async def test_keys_pressed_on_hold_are_ignored():
    """The caller presses 2 during the hold; it must not be taken for the next menu's answer."""
    result, call = await walk([("press 1", "5"), ("Please hold for the next available supervisor", "2")], tree=small())
    assert call.said[-1].startswith("All of our supervisors are busy")


async def test_a_representative_is_a_hold_and_then_a_transfer_to_the_conversational_line():
    result, call = await walk([("press 1", "0")], tree=small())
    assert result.outcome == "transfer" and call.hold_frames > 0 and call.said[-1] == "Thank you for holding."


async def test_the_caller_hanging_up_at_any_point_ends_the_walk():
    result, call = await walk([("press 1", "1"), ("six digit member", None)], tree=small(), hang_up_after=True)
    assert result.outcome == "caller_gone"


def test_hold_music_is_the_length_asked_for_and_not_silent():
    pcm = hold_music_pcm(1.0)
    assert len(pcm) == 16000 and any(pcm)
    assert hold_music_pcm(0) == b""
