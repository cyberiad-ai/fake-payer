"""The tree's spoken lines: synthesized once, kept, respelled for the voice, and a failure is silence rather than a dropped call."""
import pytest

from fake_payer import refusals
from fake_payer.config import Config
from fake_payer.prompts import Prompts

ENV = {"SONIOX_API_KEY": "k", "ANTHROPIC_API_KEY": "m", "FAKE_PAYER_NUMBER": "+12025550123", "ANSWER_KEY": "test-answer-key-0123456789"}


@pytest.fixture
def synthesized(monkeypatch):
    spoken = []

    async def fake(config, text):
        spoken.append(text)
        return b"PCM:" + text.encode()
    monkeypatch.setattr(refusals, "synthesize", fake)
    return spoken


def make(tmp_path, **env):
    return Prompts(Config.from_env({**ENV, "AUDIO_CACHE_DIR": str(tmp_path / "audio"), **env}))


async def test_a_line_is_synthesized_once_and_then_played_from_memory(tmp_path, synthesized):
    prompts = make(tmp_path)
    assert await prompts.audio("Press 1.") == b"PCM:Press 1." and await prompts.audio("Press 1.") == b"PCM:Press 1."
    assert synthesized == ["Press 1."]


async def test_a_line_kept_on_disk_is_not_synthesized_again_after_a_restart(tmp_path, synthesized):
    await make(tmp_path).audio("Press 1.")
    assert await make(tmp_path).audio("Press 1.") == b"PCM:Press 1." and synthesized == ["Press 1."]


async def test_the_voice_is_given_the_respelled_words_and_a_different_voice_has_its_own_audio(tmp_path, synthesized):
    await make(tmp_path).audio("Thank you for calling Acme Dental.")
    assert synthesized == ["Thank you for calling Ackmee Dental."]
    await make(tmp_path, TTS_VOICE="Other").audio("Thank you for calling Acme Dental.")
    assert len(synthesized) == 2


async def test_a_line_that_cannot_be_synthesized_is_silence_and_is_tried_again_next_time(tmp_path, monkeypatch):
    calls = []

    async def failing(config, text):
        calls.append(text)
        raise RuntimeError("no service")
    monkeypatch.setattr(refusals, "synthesize", failing)
    prompts = make(tmp_path)
    assert await prompts.audio("Press 1.") == b"" and await prompts.audio("Press 1.") == b""
    assert len(calls) == 2


async def test_prepare_has_every_line_ready(tmp_path, synthesized):
    prompts = make(tmp_path)
    await prompts.prepare(["One.", "Two."])
    assert synthesized == ["One.", "Two."]
    await prompts.audio("One.")
    assert synthesized == ["One.", "Two."]


async def test_two_calls_wanting_the_same_line_at_once_share_one_synthesis(tmp_path, monkeypatch):
    import asyncio
    seen = []

    async def slow(config, text):
        seen.append(text)
        await asyncio.sleep(0.05)
        return b"PCM"
    monkeypatch.setattr(refusals, "synthesize", slow)
    prompts = make(tmp_path)
    assert await asyncio.gather(prompts.audio("Press 1."), prompts.audio("Press 1.")) == [b"PCM", b"PCM"] and seen == ["Press 1."]


async def test_prepare_works_a_few_at_a_time_and_makes_each_line_once(tmp_path, monkeypatch):
    import asyncio
    running = peak = 0
    seen = []

    async def slow(config, text):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        seen.append(text)
        await asyncio.sleep(0.02)
        running -= 1
        return b"PCM"
    monkeypatch.setattr(refusals, "synthesize", slow)
    await make(tmp_path).prepare([f"Line {n}." for n in range(10)] + ["Line 1."], concurrency=3)
    assert peak == 3 and sorted(seen) == sorted(f"Line {n}." for n in range(10))
