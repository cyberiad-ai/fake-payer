import pytest

from fake_payer.speech_text import date_words, digits_spoken, dollars, months, number_words, percent


@pytest.mark.parametrize("n,words", [(0, "zero"), (7, "seven"), (15, "fifteen"), (21, "twenty-one"), (80, "eighty"), (100, "one hundred"), (880, "eight hundred eighty"),
                                      (1000, "one thousand"), (1500, "one thousand five hundred"), (9999, "nine thousand nine hundred ninety-nine")])
def test_numbers_are_written_as_words(n, words):
    assert number_words(n) == words


def test_numbers_out_of_range_are_refused():
    with pytest.raises(ValueError):
        number_words(10000)
    with pytest.raises(ValueError):
        number_words(-1)


def test_money_percent_and_months_agree_in_number():
    assert dollars(1) == "one dollar" and dollars(50) == "fifty dollars" and dollars(0) == "zero dollars"
    assert percent(80) == "eighty percent"
    assert months(1) == "one month" and months(12) == "twelve months"


@pytest.mark.parametrize("iso,words", [("2026-06-30", "June thirtieth, twenty twenty-six"), ("2025-01-01", "January first, twenty twenty-five"),
                                        ("1985-03-14", "March fourteenth, nineteen eighty-five"), ("2000-12-31", "December thirty-first, two thousand"),
                                        ("2005-07-22", "July twenty-second, two thousand five"), ("1905-02-20", "February twentieth, nineteen oh five"),
                                        ("2026-09-21", "September twenty-first, twenty twenty-six")])
def test_dates_are_said_as_a_person_would(iso, words):
    assert date_words(iso) == words


def test_digits_are_said_one_at_a_time():
    assert digits_spoken("100001") == "one zero zero zero zero one"
    assert digits_spoken("1-2") == "one two"
