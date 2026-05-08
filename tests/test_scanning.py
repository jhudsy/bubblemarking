"""Calibration math + the scan-page-level helpers that don't need an image."""
import numpy as np
import pytest

from bubblemarking.scanning import (
    Calibration,
    PageScan,
    answers_to_string,
    calibrate_from_scans,
    question_confidence,
    reclassify_with_calibration,
)


# ----------------------------------------------------------- confidence
def test_question_confidence_uniform_is_zero():
    assert question_confidence(np.array([500, 500, 500, 500, 500])) == 0.0


def test_question_confidence_clear_split_is_high():
    # One bubble much darker than the other four → big gap.
    c = question_confidence(np.array([500, 100, 500, 500, 500]))
    assert c > 0.7


def test_question_confidence_zero_max_returns_zero():
    assert question_confidence(np.array([0, 0, 0, 0, 0])) == 0.0


def test_question_confidence_handles_empty():
    assert question_confidence(np.array([])) == 0.0


# ----------------------------------------------------------- calibration
def _scan_with_brightness(brightness_per_q, answers, page_index=0):
    s = PageScan(page_index=page_index, num_questions=len(brightness_per_q))
    for q, br in enumerate(brightness_per_q, start=1):
        s.question_brightness[q] = np.array(br, dtype=float)
        s.answers[q] = list(answers.get(q, []))
    s.bars = "stub"  # mark as readable for unreadable check
    return s


def test_calibrate_returns_invalid_when_too_few_filled():
    # Only 2 filled — below min_filled threshold.
    scans = [
        _scan_with_brightness(
            [[500, 100, 500, 500, 500]],
            {1: [1]},
        ),
    ]
    cal = calibrate_from_scans(scans)
    assert cal.valid is False


def test_calibrate_learns_threshold_at_midpoint():
    # 6 filled at ~100, 24 unfilled at ~500. Need at least min_filled=5,
    # min_unfilled=50 — so build enough rows.
    scans = []
    for _ in range(15):
        scans.append(_scan_with_brightness(
            [[500, 100, 500, 500, 500]],  # B is filled
            {1: [1]},
        ))
    cal = calibrate_from_scans(scans)
    assert cal.valid is True
    assert cal.filled_median == pytest.approx(100, abs=1)
    assert cal.unfilled_median == pytest.approx(500, abs=1)
    assert cal.threshold == pytest.approx(300, abs=1)
    assert cal.spread == pytest.approx(400, abs=1)


def test_calibration_is_filled_and_margin():
    cal = Calibration(filled_median=100, unfilled_median=500,
                       threshold=300, spread=400, n_filled=10, n_unfilled=50, valid=True)
    assert cal.is_filled(50) is True
    assert cal.is_filled(550) is False
    assert cal.is_filled(300) is False  # exactly threshold counts as not-filled
    # margin = |b - 300| / (400/2) = |b - 300| / 200
    assert cal.margin(100) == pytest.approx(1.0)
    assert cal.margin(300) == pytest.approx(0.0)
    assert cal.margin(500) == pytest.approx(1.0)


def test_calibration_invalid_skips_reclassify():
    scan = _scan_with_brightness([[500, 100, 500, 500, 500]], {1: [1]})
    pre_answers = scan.answers[1].copy()
    cal = Calibration(valid=False)
    reclassify_with_calibration(scan, cal)
    assert scan.answers[1] == pre_answers  # unchanged


def test_reclassify_union_keeps_first_pass_faint_marks():
    """A bubble at 380k brightness with cohort threshold 385k should stay
    detected if first-pass labelled it filled."""
    scan = _scan_with_brightness(
        [[500, 380, 500, 500, 500]],
        {1: [1]},  # first-pass labelled B as filled
    )
    cal = Calibration(filled_median=290, unfilled_median=480,
                       threshold=385, spread=190,
                       n_filled=100, n_unfilled=500, valid=True)
    reclassify_with_calibration(scan, cal)
    # 380 < 385 so cohort-absolute alone would catch B; union still includes it.
    assert scan.answers[1] == [1]


def test_reclassify_union_adds_cohort_only_detections():
    """A bubble at 350k that first-pass missed (because its row max was
    similar) should still be picked up by the cohort threshold."""
    scan = _scan_with_brightness(
        [[400, 350, 400, 400, 400]],
        {1: []},  # first-pass found nothing (400/350 ratio ~ 0.875)
    )
    cal = Calibration(filled_median=290, unfilled_median=480,
                       threshold=385, spread=190,
                       n_filled=100, n_unfilled=500, valid=True)
    reclassify_with_calibration(scan, cal)
    assert scan.answers[1] == [1]


def test_reclassify_salvages_clear_outlier_below_blank_baseline():
    """A faint pencil mark with an in-row gap and absolute brightness on the
    filled side of midway-to-blank gets salvaged."""
    # Mimics page 4 q3 from the example: row max ~489, darkest ~395, gap ~63.
    scan = _scan_with_brightness(
        [[457, 489, 476, 395, 489]],
        {1: []},  # first-pass found nothing
    )
    cal = Calibration(filled_median=290, unfilled_median=480,
                       threshold=385, spread=190,
                       n_filled=700, n_unfilled=11000, valid=True)
    reclassify_with_calibration(scan, cal)
    assert scan.answers[1] == [3]  # the salvaged D


def test_reclassify_does_not_salvage_printer_artefact_above_baseline():
    """A bubble with a similar in-row gap but higher absolute brightness
    (closer to the blank median) is rejected — it's printer noise, not
    a faint mark."""
    # Mimics page 1 q91 from the example: row max ~510, darkest ~450, gap ~54.
    scan = _scan_with_brightness(
        [[504, 505, 510, 510, 450]],
        {1: []},
    )
    cal = Calibration(filled_median=290, unfilled_median=480,
                       threshold=385, spread=190,
                       n_filled=700, n_unfilled=11000, valid=True)
    reclassify_with_calibration(scan, cal)
    assert scan.answers[1] == []


def test_reclassify_salvage_gate_rejects_mid_zone_artefacts():
    """Bubbles with a clear in-row gap but absolute brightness in the upper
    half between threshold and blank median (e.g. p5/q37-style scanner
    noise at ~437k with cohort threshold ~385k and gate ~423k) are
    rejected. Otherwise spurious detections leak past q28 in shorter
    exams."""
    scan = _scan_with_brightness(
        [[490, 478, 487, 496, 437]],
        {1: []},
    )
    cal = Calibration(filled_median=290, unfilled_median=480,
                       threshold=385, spread=190,
                       n_filled=700, n_unfilled=11000, valid=True)
    reclassify_with_calibration(scan, cal)
    # Darkest 437 > gate (385 + 0.2 * 190 = 423) so we don't salvage.
    assert scan.answers[1] == []


def test_reclassify_does_not_salvage_uniform_blank_row():
    """All bubbles within ~1-2% of each other → nothing is salvaged."""
    scan = _scan_with_brightness(
        [[500, 504, 502, 500, 503]],
        {1: []},
    )
    cal = Calibration(filled_median=290, unfilled_median=480,
                       threshold=385, spread=190,
                       n_filled=700, n_unfilled=11000, valid=True)
    reclassify_with_calibration(scan, cal)
    assert scan.answers[1] == []


# ----------------------------------------------------------- helpers
def test_answers_to_string():
    assert answers_to_string([]) == ""
    assert answers_to_string([0]) == "A"
    assert answers_to_string([4, 0, 2]) == "A,C,E"


# ----------------------------------------------------------- PageScan
def test_pagescan_toggle_answer_one_answer_only():
    s = PageScan(page_index=0, num_questions=1, one_answer_only=True)
    s.toggle_answer(1, 0)
    assert s.answers[1] == [0]
    s.toggle_answer(1, 2)  # selecting another — should replace
    assert s.answers[1] == [2]
    s.toggle_answer(1, 2)  # toggling off
    assert s.answers[1] == []


def test_pagescan_toggle_answer_multi():
    s = PageScan(page_index=0, num_questions=1, one_answer_only=False)
    s.toggle_answer(1, 0)
    s.toggle_answer(1, 2)
    assert s.answers[1] == [0, 2]
    s.toggle_answer(1, 0)  # toggle off
    assert s.answers[1] == [2]


def test_pagescan_set_matric_digit_validates():
    s = PageScan(page_index=0)
    s.set_matric_digit(0, 5)
    assert s.matric_digits[0] == 5
    with pytest.raises(ValueError):
        s.set_matric_digit(8, 0)  # out of range
    with pytest.raises(ValueError):
        s.set_matric_digit(0, 11)  # out of range


def test_pagescan_unreadable_property():
    s = PageScan(page_index=0)
    assert s.unreadable is True  # bars=None
    s.bars = "stub"
    assert s.unreadable is False
