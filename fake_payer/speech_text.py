"""Figures, dates and digits written the way the line says them, so a speech engine reads them the same way every time (no "$1500" or "2026-06-30" for it to guess at)."""
from __future__ import annotations

import datetime
import re

_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
_MONTHS = ("January February March April May June July August September October November December").split()
_ORDINALS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth", 11: "eleventh", 12: "twelfth",
             13: "thirteenth", 14: "fourteenth", 15: "fifteenth", 16: "sixteenth", 17: "seventeenth", 18: "eighteenth", 19: "nineteenth", 20: "twentieth", 30: "thirtieth"}


def number_words(n: int) -> str:
    """0 to 9999 in words: 880 is "eight hundred eighty", 1500 is "one thousand five hundred"."""
    if not 0 <= n <= 9999:
        raise ValueError("number_words handles 0 to 9999")
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else "")
    if n < 1000:
        return f"{_ONES[n // 100]} hundred" + (f" {number_words(n % 100)}" if n % 100 else "")
    return f"{_ONES[n // 1000]} thousand" + (f" {number_words(n % 1000)}" if n % 1000 else "")


def dollars(n: int) -> str:
    return f"{number_words(n)} dollar" + ("" if n == 1 else "s")


def percent(n: int) -> str:
    return f"{number_words(n)} percent"


def months(n: int) -> str:
    return f"{number_words(n)} month" + ("" if n == 1 else "s")


def _ordinal(day: int) -> str:
    if day in _ORDINALS:
        return _ORDINALS[day]
    return f"twenty-{_ORDINALS[day - 20]}"                  # 21 to 29 (31 is below)


def _year(year: int) -> str:
    if 2000 <= year < 2010:
        return "two thousand" + (f" {_ONES[year - 2000]}" if year > 2000 else "")
    first, second = divmod(year, 100)
    return number_words(first) + (" hundred" if second == 0 else f" oh {_ONES[second]}" if second < 10 else f" {number_words(second)}")


def date_words(iso: str) -> str:
    """2026-06-30 as "June thirtieth, twenty twenty-six"."""
    day = datetime.date.fromisoformat(iso)
    spoken_day = "thirty-first" if day.day == 31 else _ordinal(day.day)
    return f"{_MONTHS[day.month - 1]} {spoken_day}, {_year(day.year)}"


def digits_spoken(digits: str) -> str:
    """"100001" as "one zero zero zero zero one"."""
    return " ".join(_ONES[int(c)] for c in digits if c.isdigit())


def numbers_in_words(text: str) -> str:
    """"3 years" as "three years": every whole number in the text (up to 9999) written out."""
    return re.sub(r"\d{1,4}", lambda m: number_words(int(m.group())), text)


def spoken_question(question: str) -> str:
    """A form question as it is read out in a menu: "Sealants D1351: coverage %" as "Sealants D one three five one: coverage percent"."""
    text = re.sub(r"\s*\(([^)]*)\)", r", \1", question)
    text = re.sub(r"\bD(\d{4})\b", lambda m: "D " + digits_spoken(m.group(1)), text)
    text = text.replace("%", " percent").replace("$", " dollars").replace("/", " or ")
    return re.sub(r"\s+", " ", text).strip()
