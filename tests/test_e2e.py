"""End-to-end smoke test using the bundled examples PDF (if present).

Skipped automatically when ``examples/`` is missing — the PDF contains
real student data and is not committed to the repo."""
import os

import pytest

from bubblemarking import scanning
from bubblemarking.dataframes import (
    AnswerKey,
    build_output_df,
    options_to_letters,
)
from bubblemarking.gui.review import recompute_duplicate_flags, recompute_flags
from bubblemarking.scoring import (
    all_or_nothing,
    default_options,
    list_builtins,
    negative_marking,
)


HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE_PDF = os.path.join(HERE, "examples", "scan_csc342_2026-05-07-11-52-37.pdf")
pytestmark = pytest.mark.skipif(
    not os.path.exists(EXAMPLE_PDF),
    reason=f"example PDF not present (looked in {EXAMPLE_PDF})",
)


@pytest.fixture(scope="module")
def cohort():
    """Scan every page of the example PDF and run cohort calibration."""
    doc = scanning.get_file(EXAMPLE_PDF)
    scans = []
    for i in range(scanning.get_number_of_pages(doc)):
        img = scanning.get_image_from_file(doc, i)
        s = scanning.scan_page(img, page_index=i)
        s.prepared_image = None
        scans.append(s)
    cal = scanning.calibrate_from_scans(scans)
    assert cal.valid, "calibration should fit on the bundled cohort"
    for s in scans:
        scanning.reclassify_with_calibration(s, cal)
        recompute_flags(s, low_conf_threshold=0.15)
    recompute_duplicate_flags(scans)
    return scans, cal


def test_every_page_has_geometry(cohort):
    scans, _ = cohort
    assert all(not s.unreadable for s in scans), \
        "every page in the bundled example should be readable"


def test_calibration_separates_filled_and_blank(cohort):
    _, cal = cohort
    assert cal.unfilled_median - cal.filled_median > 100, \
        "filled and blank populations should be visibly separated"
    # Sanity-check the threshold sits between the medians.
    assert cal.filled_median < cal.threshold < cal.unfilled_median


def test_matric_reads_succeed_on_majority(cohort):
    scans, _ = cohort
    matrics = [s.matric_string() for s in scans]
    unread = sum(1 for m in matrics if m == scanning.UNREAD_MATRIC)
    # At most a quarter of the cohort should be unfilled / unreadable matric.
    assert unread <= len(scans) // 4


def test_flag_count_is_reasonable(cohort):
    """At the default 0.15 sensitivity, fewer than 1 in 4 questions should be
    flagged across the whole cohort."""
    scans, _ = cohort
    total_flags = sum(len([f for f in s.flags if f.startswith("low_confidence")])
                      for s in scans)
    total_questions = sum(s.num_questions for s in scans)
    rate = total_flags / total_questions if total_questions else 0
    assert rate < 0.05, f"flag rate {rate:.1%} too high — review queue would be unusable"


def test_export_with_each_strategy(cohort):
    scans, _ = cohort
    # Synthesize a key from page-1 answers (just to drive the export).
    p1 = scans[0]
    keys = {q: set(ans) for q, ans in p1.answers.items() if ans}
    if not keys:
        pytest.skip("page 1 has no detected answers to seed an answer key")
    ak = AnswerKey(questions=keys)

    for strategy in list_builtins():
        opts = default_options(strategy)
        df = build_output_df(scans, ak, strategy=strategy, options=opts)
        assert "Total" in df.columns
        # One row per scan + the key row.
        assert len(df) == len(scans) + 1
        # Totals are real numbers, not NaN.
        assert df["Total"].notna().all()
        # Answer-key row's Total equals max achievable.
        max_t = df.iloc[0]["Total"]
        assert df.iloc[1:]["Total"].max() <= max_t + 1e-9, \
            "no student should out-score the answer key"


def test_export_csv_round_trip(cohort, tmp_path):
    scans, _ = cohort
    ak = AnswerKey(questions={q: set(a) for q, a in scans[0].answers.items() if a})
    df = build_output_df(scans, ak, strategy=all_or_nothing, options={})
    out = tmp_path / "results.csv"
    df.to_csv(out, index=False)
    # File written, non-empty, parses back to the same shape.
    import pandas as pd
    back = pd.read_csv(out)
    assert back.shape == df.shape
    assert list(back.columns) == list(df.columns)
