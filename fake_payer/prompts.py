"""What the phone tree says, as audio in the configured voice.

A prompt is synthesized once with Soniox and kept (in memory and in the cache directory), so a call plays stored audio: the fixed menu prompts are prepared when the line
starts, and an answer built from a member's coverage is kept the first time it is spoken (the same member always gets the same words, so each is synthesized once). A prompt
that cannot be synthesized is played as silence; the call still goes on.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Dict, Iterable

from .config import Config
from .pronunciation import respell
from . import refusals

logger = logging.getLogger(__name__)


class Prompts:
    def __init__(self, config: Config):
        self._config = config
        self._memory: Dict[str, bytes] = {}

    def _path(self, spoken: str) -> Path:
        key = f"{self._config.tts_voice}|{self._config.tts_model}|{spoken}"
        return Path(self._config.audio_cache_dir) / (hashlib.sha256(key.encode()).hexdigest()[:24] + ".pcm")

    async def audio(self, text: str) -> bytes:
        if text in self._memory:
            return self._memory[text]
        spoken = respell(text)                                   # the voice is given the respelled words ("Ackmee"); the cache is keyed on what it was given
        path = self._path(spoken)
        try:
            if path.is_file():
                pcm = path.read_bytes()
            else:
                pcm = await refusals.synthesize(self._config, spoken)
                if not pcm:
                    raise RuntimeError("no audio came back")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(pcm)
        except Exception as exc:
            logger.warning("could not prepare the spoken prompt %r: %s", text[:60], exc)
            return b""                                       # not remembered: the next call tries again
        self._memory[text] = pcm
        return pcm

    async def prepare(self, texts: Iterable[str]) -> None:
        for text in texts:
            await self.audio(text)
