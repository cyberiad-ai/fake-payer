"""Acme is said ACK-mee: the voice is given a respelling; nothing else is touched."""
import pytest

from fake_payer.pronunciation import Respeller, respell


def test_the_word_is_respelled_only_as_a_whole_word():
    assert respell("Thank you for calling Acme Dental Benefits.") == "Thank you for calling Ackmee Dental Benefits."
    assert respell("Acmes and acme and Pacmen") == "Acmes and acme and Pacmen"


@pytest.mark.parametrize("pieces", [["Acme"], ["Ac", "me"], ["A", "c", "m", "e"], ["You are on the Ac", "me Gold plan"], ["on Acm", "e."]])
def test_a_word_split_across_pieces_is_still_respelled(pieces):
    r = Respeller()
    spoken = "".join(r.feed(p) for p in pieces) + r.flush()
    assert spoken == "".join(pieces).replace("Acme", "Ackmee")


def test_the_end_of_a_piece_that_is_not_the_word_is_not_held_back():
    r = Respeller()
    assert r.feed("The plan is a") == "The plan is a"          # lower case is not the name
    assert r.feed("n option") == "n option"
    assert r.feed("Ask A") == "Ask "                           # but a capital A might still become "Acme"
    assert r.feed("nna") == "Anna"
    assert r.flush() == ""


def test_what_is_held_comes_out_at_the_end():
    r = Respeller()
    assert r.feed("Say Ac") == "Say "
    assert r.flush() == "Ac"
