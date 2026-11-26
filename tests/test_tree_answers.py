"""The tree answers whatever the conversational line can: every question on the verification form, procedure checks, services and limits, a plan by group number, the
reference number. Walked by keypad with the same scripted call as test_tree."""
import asyncio

import pytest

from fake_payer.coverage import Fixture
from fake_payer.tree import PhoneTree, TreeError, TreeRunner, form_pages, form_prompt
from tests.test_tree import FIXTURE, REAL_TREE, FakePrompts, ScriptedCall, enter, small, walk


@pytest.fixture(autouse=True)
def whole_prompts_are_one_frame(monkeypatch):
    """The test prompts are their own text; a long page would be cut into several frames mid-sentence, so each prompt is sent in one piece."""
    monkeypatch.setattr("fake_payer.tree.FRAME_BYTES", 10 ** 6)


# ---- the tree and the fixture agree --------------------------------------------------------------------------------------------------

def test_the_real_tree_and_the_fixture_agree_on_topics():
    REAL_TREE.check_against(FIXTURE)
    assert set(FIXTURE.benefit_topics()) == {"plan", "amounts", "preventive", "perio", "restorative", "ortho", "history"}
    assert sum(len(v) for v in FIXTURE.benefit_topics().values()) == len(FIXTURE.benefit_answers) == 128


def test_a_fixture_topic_no_menu_reaches_is_an_error():
    raw = {"settings": REAL_TREE.settings, "start": REAL_TREE.start, "nodes": {k: v for k, v in REAL_TREE.nodes.items() if k != "form_history"}}
    raw["nodes"]["forms"] = {**raw["nodes"]["forms"], "options": {k: v for k, v in raw["nodes"]["forms"]["options"].items() if v != "form_history"}}
    with pytest.raises(TreeError, match="no form menu reaches: history"):
        PhoneTree.from_dict(raw).check_against(FIXTURE)


def test_a_form_menu_for_a_topic_the_fixture_does_not_have_is_an_error():
    raw = {"settings": REAL_TREE.settings, "start": REAL_TREE.start, "nodes": {**REAL_TREE.nodes, "form_plan": {**REAL_TREE.nodes["form_plan"], "topic": "nothing"}}}
    with pytest.raises(TreeError, match="topics the fixture has no questions for: nothing"):
        PhoneTree.from_dict(raw).check_against(FIXTURE)


def test_every_line_the_forms_can_say_is_in_the_list_to_prepare():
    texts = set(REAL_TREE.texts(FIXTURE))
    assert all(entry["answer"] in texts for entry in FIXTURE.benefit_answers)
    node = REAL_TREE.nodes["form_preventive"]
    pages = form_pages(FIXTURE.benefit_topics()["preventive"])
    assert all(form_prompt(node, pages, page) in texts for page in range(len(pages)))
    assert len(REAL_TREE.texts()) < len(texts)                         # the fixture-free list is only the menus


def test_a_page_reads_its_questions_with_keys_and_says_how_to_go_on():
    pages = form_pages(FIXTURE.benefit_topics()["preventive"])
    first, last = form_prompt(REAL_TREE.nodes["form_preventive"], pages, 0), form_prompt(REAL_TREE.nodes["form_preventive"], pages, len(pages) - 1)
    assert first.startswith("Exams, x-rays and preventive care. For Comprehensive exam D zero one five zero: how often is it covered?, press 1.")
    assert "For more questions, press 9." in first and "To hear the first questions again, press 9." in last and first.endswith("To go back, press star.")
    assert all(len(page) <= 7 for page in pages)


# ---- every question on the form can be reached and answered ------------------------------------------------------------------------------

async def test_every_question_the_conversational_line_can_answer_is_reachable_and_answered_by_keypad():
    topics = FIXTURE.benefit_topics()
    forms_keys = {str(node["topic"]): key for key, name in REAL_TREE.nodes["forms"]["options"].items() if (node := REAL_TREE.nodes[name])["type"] == "form"}
    script = [("press 1", "6")]
    for topic, entries in topics.items():
        script.append(("For plan and network questions, press 1", forms_keys[topic]))
        pages = form_pages(entries)
        for page in pages:
            for number in range(1, len(page) + 1):
                script.append(("To go back, press star", str(number)))
            script.append(("To go back, press star", "9"))
        script += [("To go back, press star", "*")]
    result, call = await walk(script, tree=small(), hang_up_after=True, seconds=30)
    asked = [e["question"] for e in result.events if "question" in e]
    assert sorted(asked) == sorted(entry["question"] for entry in FIXTURE.benefit_answers)
    said = set(call.said)
    assert all(entry["answer"] in said for entry in FIXTURE.benefit_answers)


async def test_the_form_questions_need_no_member_and_the_last_page_wraps_to_the_first():
    topic_key = next(key for key, name in REAL_TREE.nodes["forms"]["options"].items() if name == "form_perio")
    pages = form_pages(FIXTURE.benefit_topics()["perio"])
    script = [("press 1", "6"), ("For plan and network", topic_key)] + [("To go back", "9")] * len(pages) + [("To go back", "*"), ("For plan and network", "*"), ("press 1", None)]
    result, call = await walk(script, tree=small(), hang_up_after=True)
    prompts = [line for line in call.said if line.startswith("Periodontal care.")]
    assert len(prompts) == len(pages) + 1 and prompts[0] == prompts[-1]            # round again to the first page
    assert result.outcome == "caller_gone"


async def test_a_key_that_is_not_on_the_page_is_refused_and_the_page_is_read_again():
    topic_key = next(key for key, name in REAL_TREE.nodes["forms"]["options"].items() if name == "form_ortho")
    result, call = await walk([("press 1", "6"), ("For plan and network", topic_key), ("To go back", "8"), ("not a valid option", None), ("To go back", "*")],
                              tree=small(), hang_up_after=True)
    assert "That is not a valid option." in call.said
    assert any(e.get("failure") == "invalid" and e.get("key") == "8" and e["node"] == "form_ortho" for e in result.events)
    assert sum(1 for line in call.said if line.startswith("Orthodontics.")) >= 2


# ---- services, procedure checks, groups, the reference -----------------------------------------------------------------------------------
# 2026-11-25: member-level lookup (a real member ID + date of birth, as opposed to a group number with no member) is no longer reachable by
# keypad at all -- option 1 now goes straight to the conversational line (see test_tree.py); the per-member tests that used to reach it via
# ENTER_MEMBER/member() (services-used figures, a procedure code checked against a specific member) are removed with it.

async def test_a_plan_is_found_by_group_number_with_nothing_about_a_member():
    script = [("press 1", "7")] + enter("five digit group number", "10001", "I found the plan Acme Gold Dental") + [
        ("For eligibility dates", "1"), ("Eligibility needs a member", None), ("For eligibility dates", "2"), ("annual maximum is", None), ("For eligibility dates", "6"),
        ("four digits of the procedure code", "*"), ("For eligibility dates", "3"), ("Preventive care is covered", None), ("For eligibility dates", "5"),
        ("For preventive services", "1"), ("routine exam", None)]
    result, call = await walk(script, tree=small(), hang_up_after=True)
    said = " | ".join(call.said)
    assert "Thank you. I found the plan Acme Gold Dental. Member details are not available without a member." in said
    assert "Eligibility needs a member. The plan is Acme Gold Dental." in said
    assert "The annual maximum is one thousand five hundred dollars. The deductible is fifty dollars." in said
    assert "routine exam: two per calendar year." in said and "used" not in said.split("routine exam")[1].split("|")[0]


async def test_a_group_number_that_is_not_one_is_refused_and_after_three_tries_the_call_ends():
    def attempt(then=None):
        return enter("five digit group number", "99999", then)
    script = [("press 1", "7")] + attempt("We could not find that group number") + attempt("We could not find that group number") + attempt()
    result, call = await walk(script, tree=small())
    assert result.outcome == "hangup" and call.said[-1].startswith("We are unable to find that group")


async def test_the_reference_number_and_the_id_are_given_digit_by_digit():
    call = ScriptedCall([("press 1", "8"), ("reference number for this call", None)], hang_up_after=True)
    runner = TreeRunner(small(), FIXTURE, FakePrompts(), call, reference="01234567", pace=0)
    await asyncio.wait_for(runner.run(), 5)
    assert "This is the automated provider line. The I D is three one four one. The reference number for this call is zero one two three four five six seven." in call.said
