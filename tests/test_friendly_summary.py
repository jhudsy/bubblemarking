"""Friendly issue summary in the GUI side panel + the no_answer flag rule."""
from bubblemarking.dataframes import AnswerKey
from bubblemarking.gui.review import friendly_issue_summary, recompute_flags
from bubblemarking.scanning import ANSWER_KEY_MATRIC, PageScan


def _scan_with_flags(flags, num_questions=120):
    s = PageScan(page_index=0, num_questions=num_questions)
    s.flags = flags
    return s


def test_no_flags_returns_empty():
    assert friendly_issue_summary(_scan_with_flags([])) == []


def test_unreadable_first_in_summary():
    lines = friendly_issue_summary(_scan_with_flags(["unreadable", "no_matric"]))
    assert "geometry" in lines[0].lower()
    assert any("matric" in l.lower() for l in lines)


def test_low_confidence_collapses_to_one_line():
    flags = [f"low_confidence:{q}" for q in [3, 7, 12]]
    lines = friendly_issue_summary(_scan_with_flags(flags))
    assert len(lines) == 1
    assert "3, 7, 12" in lines[0]


def test_low_confidence_truncates_long_lists():
    flags = [f"low_confidence:{q}" for q in range(1, 21)]
    lines = friendly_issue_summary(_scan_with_flags(flags))
    assert len(lines) == 1
    assert "and 14 more" in lines[0]


def test_multi_answer_separated_from_low_confidence():
    flags = ["low_confidence:5", "multi_answer:7"]
    lines = friendly_issue_summary(_scan_with_flags(flags))
    assert len(lines) == 2
    # one mentions "Worth a glance" the other "Multiple answers"
    text = " ".join(lines).lower()
    assert "worth a glance" in text
    assert "multiple answers" in text


def test_duplicate_matric():
    flags = ["duplicate_matric:51234567"]
    lines = friendly_issue_summary(_scan_with_flags(flags))
    assert "51234567" in lines[0]


def test_singular_vs_plural_phrasing():
    one = friendly_issue_summary(_scan_with_flags(["low_confidence:5"]))
    two = friendly_issue_summary(_scan_with_flags(["low_confidence:5", "low_confidence:7"]))
    # singular form
    assert "question 5" in one[0]
    assert "questions 5, 7" in two[0]


def test_no_answer_summary():
    lines = friendly_issue_summary(_scan_with_flags(
        ["no_answer:3", "no_answer:7"]))
    assert len(lines) == 1
    assert "3, 7" in lines[0]
    assert "no answer" in lines[0].lower()


# ----------------------------------------------------------- no_answer rule
def _make_scan(matric, answers, num_questions=4, **kwargs):
    s = PageScan(page_index=0, num_questions=num_questions, **kwargs)
    s.matric_digits = [int(c) for c in matric]
    s.answers = answers
    s.bars = "stub"
    # Confidence of 1.0 means low_confidence won't fire on its own.
    s.confidence = {q: 1.0 for q in range(1, num_questions + 1)}
    return s


def test_recompute_flags_no_answer_when_key_says_in_scope():
    ak = AnswerKey(questions={1: {0}, 2: {1}, 3: set()})  # q3 is out of scope
    scan = _make_scan("11111111", {1: [], 2: [1], 3: []}, num_questions=4)
    recompute_flags(scan, answer_key=ak)
    # q1 is blank but in-scope → flag. q2 is answered → no flag.
    # q3 is blank but out-of-scope (key has no correct answer) → no flag.
    # q4 is also out-of-scope.
    assert "no_answer:1" in scan.flags
    assert "no_answer:2" not in scan.flags
    assert "no_answer:3" not in scan.flags
    assert "no_answer:4" not in scan.flags


def test_recompute_flags_no_answer_skipped_without_key():
    scan = _make_scan("11111111", {1: [], 2: []})
    recompute_flags(scan, answer_key=None)
    assert not any(f.startswith("no_answer:") for f in scan.flags)


def test_recompute_flags_no_answer_skipped_on_answer_key_page():
    ak = AnswerKey(questions={1: {0}, 2: {1}})
    # The answer-key page itself shouldn't be flagged for "missing" answers
    # — it's the reference, not a student.
    scan = _make_scan(ANSWER_KEY_MATRIC, {1: [], 2: [1]})
    recompute_flags(scan, answer_key=ak)
    assert not any(f.startswith("no_answer:") for f in scan.flags)


def test_recompute_flags_no_answer_clears_after_edit():
    ak = AnswerKey(questions={1: {0}, 2: {1}})
    scan = _make_scan("11111111", {1: [], 2: []})
    recompute_flags(scan, answer_key=ak)
    assert "no_answer:1" in scan.flags
    # Student is given an answer for q1.
    scan.answers[1] = [0]
    recompute_flags(scan, answer_key=ak)
    assert "no_answer:1" not in scan.flags
    assert "no_answer:2" in scan.flags  # still missing
