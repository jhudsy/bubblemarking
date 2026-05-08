"""Friendly issue summary in the GUI side panel."""
from bubblemarking.gui.review import friendly_issue_summary
from bubblemarking.scanning import PageScan


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
