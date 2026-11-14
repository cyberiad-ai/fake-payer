"""The representative does not say goodbye until the caller has.

The persona says so, but a model that has just been thanked will sometimes answer "You're welcome. Goodbye." anyway, and by the time the hang-up tool refuses, the word has
been spoken. So the words of a farewell are taken out of what is spoken (and of what is logged) unless the caller's last words contained one.
"""
from __future__ import annotations

import re

# The caller's own goodbye, which is what allows the representative to say one back and to hang up.
CALLER_GOODBYE = re.compile(r"\b(good\s?-?\s?bye|bye(\s|-)?bye|bye|take care|have a (good|great|nice|wonderful) (day|one|evening|afternoon))\b", re.I)

_FAREWELL = re.compile(
    r"(?P<comma>,\s*|\s+and\s+|(?<=\s)and\s+)?\b(?:good\s?-?\s?bye|bye(?:[\s-]?bye)?|take\s+care|have\s+a\s+(?:good|great|nice|wonderful|lovely)\s+(?:day|one|evening|afternoon|weekend))\b[.!,;]*\s*",
    re.I)

_ADJECTIVES = ("good", "great", "nice", "wonderful", "lovely")
_TIMES = ("day", "one", "evening", "afternoon", "weekend")
_PHRASES = ("goodbye", "good bye", "good-bye", "bye", "bye bye", "bye-bye", "take care", *(f"have a {a} {t}" for a in _ADJECTIVES for t in _TIMES))
_LONGEST = max(len(p) for p in _PHRASES) + 6          # room for the ", " or " and " in front of one


def _could_become_a_farewell(tail: str) -> bool:
    """The end of the text so far is the beginning of a farewell that has not been finished yet ("Good", "have a gr", ", take"), so it must wait for the next piece."""
    t = re.sub(r"\s+", " ", tail.lower()).lstrip(", ")
    if not t:
        return False
    if re.fullmatch(r"(?:a|an|and)", t):                  # "and" may be the start of "and take care"
        return True
    t = re.sub(r"^and ", "", t)
    return bool(t) and any(p.startswith(t) for p in _PHRASES)


def _held_length(text: str) -> int:
    """How many characters at the end of `text` may still turn into a farewell: the longest such tail that starts at a word boundary, or 0."""
    for k in range(min(len(text), _LONGEST), 0, -1):
        starts_a_word = k == len(text) or not text[-k - 1].isalnum() or not text[-k].isalnum()
        if starts_a_word and _could_become_a_farewell(text[-k:]):
            return k
    return 0


def _replace(match: "re.Match[str]") -> str:
    return "." if match.group("comma") else ""


def strip_farewell(text: str) -> str:
    """`text` without the words of a farewell ("Thank you, goodbye." becomes "Thank you."); other text is left exactly as it was."""
    cleaned = _FAREWELL.sub(_replace, text)
    return re.sub(r"\s+(?:and|but)\s*$", ".", cleaned) if cleaned != text else cleaned


def caller_said_goodbye(text: str) -> bool:
    return bool(CALLER_GOODBYE.search(text or ""))


class FarewellFilter:
    """Takes the model's text in the pieces it arrives in and gives back what may be spoken. When a goodbye is allowed everything passes straight through; otherwise only an
    ending that could still turn into a farewell is held back, until the next piece shows what it is. Everything else is spoken at once."""

    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self._held = ""
        self._after_a_farewell = False         # a farewell was removed at the very end of what has arrived: the full stop that follows it must go too

    def feed(self, chunk: str) -> str:
        if self.allowed:
            return chunk
        if self._after_a_farewell and chunk:
            chunk = re.sub(r"^[.!,;]+\s*", "", chunk)
            self._after_a_farewell = False
        combined = self._held + chunk
        self._after_a_farewell = self._after_a_farewell or any(m.end() == len(combined) for m in _FAREWELL.finditer(combined))
        self._held = strip_farewell(combined)
        keep = _held_length(self._held)
        speak, self._held = self._held[: len(self._held) - keep], self._held[len(self._held) - keep:]
        return speak

    def flush(self) -> str:
        """What is left when the reply is over."""
        if self.allowed:
            return ""
        speak, self._held = strip_farewell(self._held), ""
        return speak
