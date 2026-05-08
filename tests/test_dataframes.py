"""Round-trips and invariants for the answer-key + results-CSV layer."""
import io

import pandas as pd
import pytest

from bubblemarking.dataframes import (
    AnswerKey,
    build_output_df,
    extract_answer_key_from_scans,
    letters_to_options,
    max_total,
    options_to_letters,
    read_answer_key_from_file,
    score_scan,
)
from bubblemarking.scanning import ANSWER_KEY_MATRIC, PageScan
from bubblemarking.scoring import all_or_nothing, negative_marking, partial_credit


# ----------------------------------------------------------- letter helpers
@pytest.mark.parametrize("opts,letters", [
    ([], ""),
    ([0], "A"),
    ([0, 2, 4], "A,C,E"),
    ([4, 2, 0], "A,C,E"),  # sorted on output
])
def test_options_to_letters(opts, letters):
    assert options_to_letters(opts) == letters


@pytest.mark.parametrize("text,opts", [
    ("", []),
    (None, []),
    ("A", [0]),
    ("a,c,e", [0, 2, 4]),  # case-insensitive
    (" A , C , E ", [0, 2, 4]),  # whitespace tolerated
    ("A,A,B", [0, 1]),  # dedup
    ("A,Z,B", [0, 1, 25]),  # out-of-range letters kept as raw indices
])
def test_letters_to_options(text, opts):
    assert letters_to_options(text) == opts


def test_letters_round_trip():
    for opts in [[], [0], [1, 3], [0, 1, 2, 3, 4]]:
        assert letters_to_options(options_to_letters(opts)) == opts


# ----------------------------------------------------------- AnswerKey
def test_answer_key_weight_default():
    ak = AnswerKey(questions={1: {0, 1}}, weights={})
    assert ak.weight_for(1) == 1.0
    assert ak.weight_for(99) == 1.0


def test_answer_key_weight_overrides():
    ak = AnswerKey(questions={1: {0}}, weights={1: 2.5})
    assert ak.weight_for(1) == 2.5


def test_answer_key_num_questions():
    assert AnswerKey().num_questions == 0
    assert AnswerKey(questions={1: set(), 5: {0}}).num_questions == 5


# ----------------------------------------------------------- file loader
def test_read_answer_key_csv(tmp_path):
    p = tmp_path / "key.csv"
    p.write_text('1,"A,B,E",2\n2,"A"\n3,"C,D",1.5\n')
    ak = read_answer_key_from_file(str(p))
    assert ak.questions == {1: {0, 1, 4}, 2: {0}, 3: {2, 3}}
    assert ak.weight_for(1) == 2.0
    assert ak.weight_for(2) == 1.0  # default
    assert ak.weight_for(3) == 1.5


def test_read_answer_key_skips_header(tmp_path):
    p = tmp_path / "key.csv"
    p.write_text('Q,Answer,Weight\n1,"A",1\n2,"B",1\n')
    ak = read_answer_key_from_file(str(p))
    assert ak.questions == {1: {0}, 2: {1}}


def test_read_answer_key_xlsx(tmp_path):
    p = tmp_path / "key.xlsx"
    df = pd.DataFrame([[1, "A,B"], [2, "C"]])
    df.to_excel(str(p), index=False, header=False)
    ak = read_answer_key_from_file(str(p))
    assert ak.questions == {1: {0, 1}, 2: {2}}


def test_read_answer_key_ignores_bad_weight(tmp_path):
    p = tmp_path / "key.csv"
    p.write_text('1,"A","not-a-number"\n')
    ak = read_answer_key_from_file(str(p))
    assert ak.weight_for(1) == 1.0  # fell back to default


# ----------------------------------------------------------- key-from-scans
def _scan(matric, answers, page_index=0, num_questions=3):
    s = PageScan(page_index=page_index, num_questions=num_questions)
    s.matric_digits = [int(c) for c in matric]
    s.answers = answers
    return s


def test_extract_answer_key_from_scans():
    scans = [
        _scan("12345678", {1: [0]}),
        _scan(ANSWER_KEY_MATRIC, {1: [0, 1], 2: [2], 3: []}, num_questions=3),
    ]
    ak = extract_answer_key_from_scans(scans)
    assert ak is not None
    # Trailing empty questions are trimmed.
    assert ak.questions == {1: {0, 1}, 2: {2}}


def test_extract_answer_key_returns_none_when_absent():
    scans = [_scan("12345678", {1: [0]})]
    assert extract_answer_key_from_scans(scans) is None


# ----------------------------------------------------------- output dataframe
def test_build_output_df_columns_without_strategy():
    ak = AnswerKey(questions={1: {0, 1}, 2: {2}}, weights={1: 2.0, 2: 1.0})
    s1 = _scan("11111111", {1: [0, 1], 2: [2]}, num_questions=2)
    df = build_output_df([s1], ak)
    assert "Total" not in df.columns
    assert df.iloc[0]["Matriculation number"] == ANSWER_KEY_MATRIC
    assert df.iloc[1]["Matriculation number"] == "11111111"
    assert df.iloc[1]["Question1NumCorrect"] == 2
    assert df.iloc[1]["Question1NumIncorrect"] == 0
    assert df.iloc[1]["Question1Answer"] == "A,B"
    assert df.iloc[1]["Question1Weight"] == 2.0


def test_build_output_df_with_strategy_adds_total():
    ak = AnswerKey(questions={1: {0, 1}, 2: {2}}, weights={1: 2.0, 2: 1.0})
    s1 = _scan("11111111", {1: [0, 1], 2: [2]}, num_questions=2)  # all correct
    s2 = _scan("22222222", {1: [0], 2: [3]}, num_questions=2)  # 1 partial, 1 wrong
    df = build_output_df([s1, s2], ak,
                          strategy=negative_marking,
                          options={"penalty_per_wrong": 0.25,
                                   "penalise_partial_correct": True})
    assert "Total" in df.columns
    # Answer-key row holds maximum.
    assert df.iloc[0]["Total"] == 3.0
    assert df.iloc[1]["Total"] == 3.0  # all-correct student gets max
    # Wrong-answer student: q1 has 1 correct selected so all-or-nothing fails;
    # selected != correct and at least one wrong → penalty.
    assert df.iloc[2]["Total"] < 0  # negative due to penalty


def test_build_output_df_handles_unread_matric():
    ak = AnswerKey(questions={1: {0}})
    s1 = _scan("99999999", {1: [0]}, num_questions=1)
    s2 = _scan("99999999", {1: [0]}, num_questions=1, page_index=1)
    df = build_output_df([s1, s2], ak)
    matrics = df["Matriculation number"].tolist()
    # Two unread pages get distinct fallback IDs.
    assert len(set(matrics)) == 3  # answer key + 2 distinct fallbacks
    assert ANSWER_KEY_MATRIC in matrics


# ----------------------------------------------------------- scoring helpers
def test_score_scan_max_total_relationship():
    ak = AnswerKey(questions={1: {0}, 2: {1, 2}}, weights={1: 1.0, 2: 2.0})
    perfect = _scan("00000001", {1: [0], 2: [1, 2]}, num_questions=2)
    blank = _scan("00000002", {1: [], 2: []}, num_questions=2)
    opts = {}
    mx = max_total(ak, all_or_nothing, opts)
    assert score_scan(perfect, ak, all_or_nothing, opts) == mx
    assert score_scan(blank, ak, all_or_nothing, opts) == 0.0
