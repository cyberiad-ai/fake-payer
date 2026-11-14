import pytest

from fake_payer.farewell import FarewellFilter, caller_said_goodbye, strip_farewell


@pytest.mark.parametrize("said, kept", [
    ("You're welcome. Goodbye.", "You're welcome. "),
    ("Thank you, goodbye.", "Thank you."),
    ("Thanks for calling. Have a great day!", "Thanks for calling. "),
    ("Take care.", ""),
    ("Bye-bye for now.", "for now."),
    ("Goodbye and take care.", "."),
    ("The plan pays fifty percent.", "The plan pays fifty percent."),
    ("A good byline is not a farewell.", "A good byline is not a farewell."),
    ("Nobody said goodbyes here either.", "Nobody said goodbyes here either."),
])
def test_the_words_of_a_farewell_are_taken_out_and_nothing_else(said, kept):
    assert strip_farewell(said) == kept


def test_what_counts_as_the_callers_goodbye():
    for yes in ("Thank you, goodbye.", "ok bye", "Have a good day", "good-bye", "Take care!"):
        assert caller_said_goodbye(yes)
    for no in ("Thank you, Dana.", "I have no more questions.", "", None):
        assert not caller_said_goodbye(no)


def stream(pieces, allowed=False):
    f = FarewellFilter(allowed)
    spoken = [f.feed(piece) for piece in pieces]
    return spoken, "".join(spoken) + f.flush()


def test_a_farewell_split_across_pieces_is_still_caught():
    spoken, whole = stream(["You're welcome. Good", "bye", "."])
    assert whole == "You're welcome. "                        # and the full stop that followed it went with it
    assert "Good" not in "".join(spoken)                      # the first half was never spoken on its own


def test_ordinary_text_is_spoken_at_once_not_held_back():
    spoken, whole = stream(["The plan ", "pays fifty ", "percent for orthodontics."])
    assert spoken == ["The plan ", "pays fifty ", "percent for orthodontics."] and whole == "".join(spoken)


def test_only_an_ending_that_could_become_a_farewell_waits():
    f = FarewellFilter(False)
    assert f.feed("I can help with that. Hav") == "I can help with that."           # " Hav" might be the start of "Have a good day"
    assert f.feed("e you tried the portal?") == " Have you tried the portal?"             # it was not: released with the rest
    assert f.flush() == ""


def test_a_held_ending_that_never_completes_is_spoken_when_the_reply_ends():
    f = FarewellFilter(False)
    assert f.feed("Let me check on that and") == "Let me check on that"
    assert f.flush() == " and"


def test_when_the_caller_said_goodbye_everything_passes_unchanged():
    spoken, whole = stream(["You're welcome. ", "Goodbye."], allowed=True)
    assert spoken == ["You're welcome. ", "Goodbye."] and whole == "You're welcome. Goodbye."


def test_the_whole_reply_from_the_real_call_loses_only_its_goodbye():
    spoken, whole = stream(["You're ", "welcome", ". ", "Good", "bye", "."])
    assert whole == "You're welcome. "
