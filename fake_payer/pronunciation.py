"""How the voice is told to say certain words. The text spoken is respelled; the transcript and the log keep the real spelling."""
from __future__ import annotations

import re

RESPELLED = {"Acme": "Ackmee"}               # "ACK-mee", stress on the first syllable

_WORDS = re.compile(r"\b(" + "|".join(RESPELLED) + r")\b")


def respell(text: str) -> str:
    return _WORDS.sub(lambda m: RESPELLED[m.group(1)], text)


class Respeller:
    """The same for text that arrives in pieces: the end of a piece that could still become one of the words ("Ac") waits for the next piece."""

    def __init__(self) -> None:
        self._held = ""

    def feed(self, chunk: str) -> str:
        combined = self._held + chunk
        keep = 0
        for word in RESPELLED:
            for k in range(min(len(word) - 1, len(combined)), 0, -1):
                if combined.endswith(word[:k]) and (k == len(combined) or not combined[-k - 1].isalnum()):
                    keep = max(keep, k)
                    break
        self._held = combined[len(combined) - keep:] if keep else ""
        return respell(combined[: len(combined) - keep])

    def flush(self) -> str:
        speak, self._held = respell(self._held), ""
        return speak
