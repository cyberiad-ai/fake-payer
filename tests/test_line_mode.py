"""Which line answers a call (the representative, the phone tree, or either at random), and the tree over a real websocket: keys arrive as Vonage's `websocket:dtmf` frames."""
import json
import random

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from fake_payer.config import Config, ConfigError
from fake_payer.pronunciation import respell
from fake_payer.server import TRANSFER_GREETING, FakePayerServer
from tests.test_bridge import until
from tests.test_server import CONFIG, KEY, counted, legs, running, ws_url          # noqa: F401  (fixtures)
from tests.test_tree import FakePrompts, small

BASE = {"SONIOX_API_KEY": "k", "ANTHROPIC_API_KEY": "m", "FAKE_PAYER_NUMBER": "+12025550123", "ANSWER_KEY": KEY, "RING_COUNT": "0", "ANSWER_DELAY_SECONDS": "0",
        "REPLY_DELAY_SECONDS": "0"}


def config(tmp_path, **env):
    return Config.from_env({**BASE, "CALL_LOG_DIR": str(tmp_path / "calls"), **env})


def key_frame(digit):
    return json.dumps({"event": "websocket:dtmf", "digit": digit, "duration": 260})


class Chance:
    """A random number generator that gives the numbers it is told to. `choice` always takes the first option offered -- for a menu with `variants`, that is the base
    node's own wording/numbering (see TreeRunner._effective_node's `[None] + variants`), so a test that does not care about menu-wording variation stays deterministic."""

    def __init__(self, *values):
        self.values = list(values)

    def random(self):
        return self.values.pop(0)

    def choice(self, seq):
        return seq[0]


# ---- configuration ----------------------------------------------------------------------------------------------------------------------

def test_the_line_is_mixed_half_and_half_unless_told_otherwise():
    assert Config.from_env(BASE).line_mode == "mixed" and Config.from_env(BASE).tree_fraction == 0.5


@pytest.mark.parametrize("mode", ["conversation", "tree", "mixed", " Tree "])
def test_each_mode_can_be_set(mode):
    assert Config.from_env({**BASE, "LINE_MODE": mode}).line_mode == mode.strip().lower()


@pytest.mark.parametrize("env", [{"LINE_MODE": "both"}, {"TREE_FRACTION": "1.5"}, {"TREE_FRACTION": "-0.1"}, {"TREE_FRACTION": "half"}])
def test_a_bad_mode_or_fraction_stops_the_line_starting(env):
    with pytest.raises(ConfigError):
        Config.from_env({**BASE, **env})


@pytest.mark.parametrize("fraction", ["0", "0.25", "1"])
def test_the_fraction_may_be_anything_from_none_to_all(fraction):
    assert Config.from_env({**BASE, "TREE_FRACTION": fraction}).tree_fraction == float(fraction)


# ---- choosing -------------------------------------------------------------------------------------------------------------------------

def mode_of(tmp_path, chance=None, **env):
    return FakePayerServer(config(tmp_path, **env), tree=small(), prompts=FakePrompts(), chance=chance)._choose_mode()


def test_conversation_mode_never_uses_the_tree_and_tree_mode_always_does(tmp_path):
    assert {mode_of(tmp_path, Chance(0.0), LINE_MODE="conversation") for _ in range(3)} == {"conversation"}
    assert {mode_of(tmp_path, Chance(0.99), LINE_MODE="tree") for _ in range(3)} == {"tree"}


def test_mixed_mode_gives_the_tree_the_calls_below_the_fraction(tmp_path):
    assert mode_of(tmp_path, Chance(0.49), LINE_MODE="mixed") == "tree"
    assert mode_of(tmp_path, Chance(0.5), LINE_MODE="mixed") == "conversation"
    assert mode_of(tmp_path, Chance(0.7), LINE_MODE="mixed", TREE_FRACTION="0.8") == "tree"
    assert mode_of(tmp_path, Chance(0.0), LINE_MODE="mixed", TREE_FRACTION="0") == "conversation"
    assert mode_of(tmp_path, Chance(0.999), LINE_MODE="mixed", TREE_FRACTION="1") == "tree"


def test_mixed_mode_is_about_half_and_half_over_many_calls(tmp_path):
    server = FakePayerServer(config(tmp_path, LINE_MODE="mixed"), tree=small(), prompts=FakePrompts(), chance=random.Random(7))
    modes = [server._choose_mode() for _ in range(2000)]
    assert 0.45 < modes.count("tree") / len(modes) < 0.55


# ---- the tree over the websocket -----------------------------------------------------------------------------------------------------

async def hear(call):
    """Everything the line says until it hangs up: the text of each line (frames of text; the hold music is skipped)."""
    said = []
    try:
        async for frame in call:
            if isinstance(frame, bytes) and frame[:1].isalpha():
                said.append(frame.decode())
    except ConnectionClosed:
        pass
    return said


async def test_a_tree_call_takes_vonage_keys_answers_by_menu_and_hangs_up(counted, tmp_path):
    server = FakePayerServer(config(tmp_path, LINE_MODE="tree"), tree=small(), prompts=FakePrompts(), tree_pace=0, chance=Chance())
    context, port = await running(server)
    try:
        async with connect(await ws_url(port, "tree-1")) as call:
            await call.send(json.dumps({"event": "websocket:connected"}))              # a control frame that is not a key
            await call.send(b"\x00\x01" * 160)                                          # the caller's audio is ignored by the tree
            first = await call.recv()
            assert b"Acme Dental Benefits" in first
            await call.send(key_frame("2"))
            said = await hear(call)
        assert any(line.startswith("Claim status is not available") for line in said)
        assert counted["stt"] == [] and counted["tts"] == []                           # no speech service was touched
        await until(lambda: list((tmp_path / "calls").glob("*.json")))
        [entry] = [json.loads(p.read_text()) for p in (tmp_path / "calls").glob("*.json")]
        assert entry["mode"] == "tree" and entry["ended"] == "tree_ended" and entry["turns"] == []
        assert [e["key"] for e in entry["events"] if "key" in e] == ["2"]
        assert [e["node"] for e in entry["events"] if e.get("visit")] == ["greeting", "main", "claims"]
    finally:
        await context.__aexit__(None, None, None)


async def test_a_key_that_is_not_one_the_keypad_has_is_ignored(counted, tmp_path):
    server = FakePayerServer(config(tmp_path, LINE_MODE="tree"), tree=small(), prompts=FakePrompts(), tree_pace=0, chance=Chance())
    context, port = await running(server)
    try:
        async with connect(await ws_url(port, "tree-2")) as call:
            await call.recv()
            for bad in ("A", "12", "", None, 5):
                await call.send(json.dumps({"event": "websocket:dtmf", "digit": bad}))
            await call.send("not json")
            await call.send(key_frame("2"))
            said = await hear(call)
        assert not any("not a valid option" in line for line in said) and any("Claim status" in line for line in said)
    finally:
        await context.__aexit__(None, None, None)


async def test_a_caller_who_hangs_up_during_the_tree_is_logged_as_such(counted, tmp_path):
    server = FakePayerServer(config(tmp_path, LINE_MODE="tree"), tree=small(), prompts=FakePrompts(), tree_pace=0, chance=Chance())
    context, port = await running(server)
    try:
        async with connect(await ws_url(port, "tree-3")) as call:
            await call.recv()
        await until(lambda: list((tmp_path / "calls").glob("*.json")))
        [entry] = [json.loads(p.read_text()) for p in (tmp_path / "calls").glob("*.json")]
        assert entry["mode"] == "tree" and entry["ended"] == "caller_hung_up"
    finally:
        await context.__aexit__(None, None, None)


async def test_pressing_zero_holds_and_then_the_representative_takes_the_call(legs, tmp_path):
    server = FakePayerServer(config(tmp_path, LINE_MODE="tree"), tree=small(), prompts=FakePrompts(), tree_pace=0, system_prompt="persona", chance=Chance())
    context, port = await running(server)
    try:
        async with connect(await ws_url(port, "tree-4")) as call:
            await call.recv()
            await call.send(key_frame("0"))
            await until(lambda: "tts" in legs and legs["tts"].synthesized)
            assert legs["tts"].synthesized == [respell(TRANSFER_GREETING)]                       # a short greeting, no second disclosure and no ringing
    finally:
        await context.__aexit__(None, None, None)
    await until(lambda: list((tmp_path / "calls").glob("*.json")))
    [entry] = [json.loads(p.read_text()) for p in (tmp_path / "calls").glob("*.json")]
    assert entry["mode"] == "tree" and [e["node"] for e in entry["events"] if e.get("visit")][-1] == "representative_transfer"
    assert entry["turns"][0]["text"] == TRANSFER_GREETING


async def test_in_conversation_mode_keys_do_nothing_and_the_log_says_so(legs, tmp_path):
    server = FakePayerServer(config(tmp_path, LINE_MODE="conversation"), tree=small(), prompts=FakePrompts(), system_prompt="persona")
    context, port = await running(server)
    try:
        async with connect(await ws_url(port, "conv-1")) as call:
            await until(lambda: "tts" in legs and legs["tts"].synthesized)
            await call.send(key_frame("2"))
    finally:
        await context.__aexit__(None, None, None)
    await until(lambda: list((tmp_path / "calls").glob("*.json")))
    [entry] = [json.loads(p.read_text()) for p in (tmp_path / "calls").glob("*.json")]
    assert entry["mode"] == "conversation" and entry["events"] == []


async def test_a_caller_who_hangs_up_while_the_tree_is_speaking_is_not_an_error(counted, tmp_path, caplog):
    """The line is speaking a long prompt, a frame at a time; the caller closes the call after the first. It is logged as the caller hanging up, with the path so far."""
    from fake_payer.audio import FRAME_BYTES

    class Long(FakePrompts):
        async def audio(self, text):
            return b"A" * FRAME_BYTES * 200

    server = FakePayerServer(config(tmp_path, LINE_MODE="tree"), tree=small(), prompts=Long(), tree_pace=0.005)
    context, port = await running(server)
    try:
        async with connect(await ws_url(port, "tree-5")) as call:
            await call.recv()
            for digit in "12345678":                                 # keys queued behind the hang-up: the line takes one item a frame, so it meets a closed socket first
                await call.send(key_frame(digit))
        await until(lambda: list((tmp_path / "calls").glob("*.json")))
        [entry] = [json.loads(p.read_text()) for p in (tmp_path / "calls").glob("*.json")]
        assert entry["mode"] == "tree" and entry["ended"] == "caller_hung_up" and entry["events"] and entry["events"][0]["node"] == "greeting"
        assert not [r for r in caplog.records if r.levelname == "ERROR"]
    finally:
        await context.__aexit__(None, None, None)
