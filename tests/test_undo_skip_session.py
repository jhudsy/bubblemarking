"""Skip-from-export filtering, undo/redo state machine, snapshot helpers."""
import pytest

from bubblemarking.dataframes import AnswerKey, build_output_df
from bubblemarking.gui.review import (
    UndoEntry,
    UndoManager,
    restore_scan_snapshot,
    scan_snapshot,
)
from bubblemarking.scanning import ANSWER_KEY_MATRIC, PageScan


def _scan(matric, answers, page_index=0, num_questions=2, **kwargs):
    s = PageScan(page_index=page_index, num_questions=num_questions, **kwargs)
    s.matric_digits = [int(c) for c in matric]
    s.answers = answers
    return s


# ----------------------------------------------------------- skip
def test_build_output_df_skips_marked_pages():
    ak = AnswerKey(questions={1: {0}, 2: {1}}, weights={})
    s_in = _scan("11111111", {1: [0], 2: [1]}, page_index=0)
    s_skip = _scan("22222222", {1: [0]}, page_index=1)
    s_skip.skip_from_export = True
    df = build_output_df([s_in, s_skip], ak)
    matrics = df["Matriculation number"].tolist()
    assert "11111111" in matrics
    assert "22222222" not in matrics
    # Answer key row + one student row.
    assert len(df) == 2


def test_skip_default_false_on_pagescan():
    s = PageScan(page_index=0)
    assert s.skip_from_export is False


# ----------------------------------------------------------- snapshot
def test_snapshot_round_trip():
    s = _scan("12345678", {1: [0, 2], 2: [1]}, num_questions=3)
    s.skip_from_export = True
    snap = scan_snapshot(s)
    # Mutate the scan, then restore from snapshot.
    s.matric_digits = [9, 9, 9, 9, 9, 9, 9, 9]
    s.answers = {1: [4]}
    s.skip_from_export = False
    restore_scan_snapshot(s, snap)
    assert s.matric_digits == [1, 2, 3, 4, 5, 6, 7, 8]
    assert s.answers == {1: [0, 2], 2: [1]}
    assert s.skip_from_export is True


def test_snapshot_independent_of_source():
    s = _scan("00000000", {1: [0]})
    snap = scan_snapshot(s)
    # Mutating the scan must not change the snapshot.
    s.answers[1].append(1)
    s.matric_digits[0] = 9
    assert snap["answers"][1] == [0]
    assert snap["matric_digits"][0] == 0


# ----------------------------------------------------------- undo manager
def test_undo_redo_returns_and_pops_correctly():
    m = UndoManager()
    e1 = UndoEntry(0, "first", {"a": 1}, {"a": 2})
    e2 = UndoEntry(1, "second", {"b": 1}, {"b": 2})
    assert m.can_undo() is False
    m.push(e1)
    m.push(e2)
    assert m.can_undo() is True
    assert m.can_redo() is False
    assert m.undo() is e2
    assert m.can_redo() is True
    assert m.redo() is e2
    # Undo twice → both available for redo.
    m.undo(); m.undo()
    assert m.can_undo() is False
    # Pushing a new edit clears the redo stack.
    m.push(UndoEntry(0, "new", {}, {}))
    assert m.can_redo() is False


def test_undo_manager_clear():
    m = UndoManager()
    m.push(UndoEntry(0, "x", {}, {}))
    m.undo()
    m.clear()
    assert not m.can_undo()
    assert not m.can_redo()


def test_undo_manager_respects_max_depth():
    m = UndoManager(max_depth=2)
    m.push(UndoEntry(0, "1", {}, {}))
    m.push(UndoEntry(0, "2", {}, {}))
    m.push(UndoEntry(0, "3", {}, {}))
    # Oldest dropped; only 2 left.
    assert m._undo[0].description == "2"
    assert m._undo[1].description == "3"
