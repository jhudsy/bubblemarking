"""Answer-key loading and results-CSV construction.

Letters (``A``–``E``) are the user-visible representation; option indices
(``0``–``4``) are the internal one used by :class:`scanning.PageScan`. The
helpers :func:`options_to_letters` and :func:`letters_to_options` cross
that boundary. :func:`build_output_df` is the single source of truth for
the export schema."""
import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from bubblemarking.scanning import (
    ANSWER_KEY_MATRIC,
    UNREAD_MATRIC,
    PageScan,
)


def options_to_letters(options) -> str:
    """[0, 2, 4] -> 'A,C,E'. Empty -> ''."""
    return ",".join(chr(65 + o) for o in sorted(options))


def letters_to_options(text) -> list:
    """'A,C,E' -> [0, 2, 4]. Tolerant of spaces and empty input."""
    if text is None:
        return []
    s = str(text).strip()
    if not s:
        return []
    out = []
    for c in s.replace(" ", "").split(","):
        if not c:
            continue
        u = c.upper()
        if "A" <= u <= "Z":
            out.append(ord(u) - 65)
    return sorted(set(out))


@dataclass
class AnswerKey:
    """Correct answers + per-question weights.

    ``questions[q]`` is the set of correct option indices.
    ``weights[q]`` is the question's worth (default 1.0)."""
    questions: dict = field(default_factory=dict)
    weights: dict = field(default_factory=dict)

    def __len__(self):
        return len(self.questions)

    @property
    def num_questions(self) -> int:
        return max(self.questions.keys()) if self.questions else 0

    def correct_for(self, q: int) -> set:
        return self.questions.get(q, set())

    def weight_for(self, q: int) -> float:
        return float(self.weights.get(q, 1.0))


def read_answer_key_from_file(filename) -> AnswerKey:
    """Read a CSV/XLSX answer key. Columns:

    1. Question number
    2. Comma-separated correct letters (e.g. ``"A,C"``)
    3. (optional) Question weight — default 1.0

    A header row is tolerated and skipped."""
    try:
        df = pd.read_csv(filename, header=None)
    except Exception:
        df = pd.read_excel(filename, header=None)

    if df.empty:
        return AnswerKey()
    # Skip header row if present.
    try:
        int(df.iloc[0, 0])
    except (ValueError, TypeError):
        df = df.iloc[1:]

    questions = {}
    weights = {}
    for _, row in df.iterrows():
        try:
            q = int(row.iloc[0])
        except (ValueError, TypeError):
            continue
        questions[q] = set(letters_to_options(row.iloc[1]))
        if len(row) >= 3 and pd.notna(row.iloc[2]):
            try:
                weights[q] = float(row.iloc[2])
            except (ValueError, TypeError):
                logging.warning(f"Question {q}: ignoring non-numeric weight {row.iloc[2]!r}")
    return AnswerKey(questions=questions, weights=weights)


def extract_answer_key_from_scans(scans) -> Optional[AnswerKey]:
    """Pull the answer key off the page whose matric reads as 00000000.
    Weights default to 1.0 since scans don't carry weight information."""
    for s in scans:
        if s.matric_string() == ANSWER_KEY_MATRIC:
            # Trim trailing empty questions — many sheets have <120 questions.
            keys = {}
            last_filled = 0
            for q in sorted(s.answers.keys()):
                if s.answers[q]:
                    last_filled = q
            for q in sorted(s.answers.keys()):
                if q <= last_filled:
                    keys[q] = set(s.answers[q])
            return AnswerKey(questions=keys)
    return None


def question_marks(selected: set, correct: set):
    """Returns (num_correct_selected, num_incorrect_selected)."""
    return len(selected & correct), len(selected - correct)


def score_scan(scan: PageScan, answer_key: AnswerKey, strategy, options=None,
               num_options: int = 5) -> float:
    """Apply a scoring strategy across every question in the answer key
    and return the student's total."""
    options = options or {}
    total = 0.0
    for q in range(1, answer_key.num_questions + 1):
        correct = answer_key.correct_for(q)
        if not correct:
            continue
        selected = set(scan.answers.get(q, []))
        weight = answer_key.weight_for(q)
        total += float(strategy.score(selected, correct, weight, num_options, **options))
    return total


def max_total(answer_key: AnswerKey, strategy, options=None, num_options: int = 5) -> float:
    """The maximum achievable total under this strategy — i.e. the score the
    answer key itself would get."""
    options = options or {}
    total = 0.0
    for q in range(1, answer_key.num_questions + 1):
        correct = answer_key.correct_for(q)
        if not correct:
            continue
        weight = answer_key.weight_for(q)
        total += float(strategy.score(correct, correct, weight, num_options, **options))
    return total


def build_output_df(scans, answer_key: AnswerKey, strategy=None,
                    options=None, num_options: int = 5) -> pd.DataFrame:
    """Build the per-student results dataframe. Row 0 is the answer key.

    Duplicate or unread matric numbers are renumbered to 9999999x to keep the
    table unambiguous; the GUI surfaces these as "needs review" for the user
    to fix interactively before export.

    If ``strategy`` is given, a ``Total`` column is added; the answer-key row
    holds the maximum achievable total."""
    nq = answer_key.num_questions
    rows = []

    key_row = {"Matriculation number": ANSWER_KEY_MATRIC}
    for q in range(1, nq + 1):
        correct = answer_key.correct_for(q)
        key_row[f"Question{q}NumCorrect"] = len(correct)
        key_row[f"Question{q}NumIncorrect"] = 0
        key_row[f"Question{q}Answer"] = options_to_letters(correct)
        key_row[f"Question{q}Weight"] = answer_key.weight_for(q)
    if strategy is not None:
        key_row["Total"] = max_total(answer_key, strategy, options, num_options)
    rows.append(key_row)

    seen = set()
    fallback = 99999999
    for s in scans:
        if getattr(s, "skip_from_export", False):
            logging.info(f"Skipping page {s.page_index + 1} from export (user-marked).")
            continue
        matric = s.matric_string()
        if matric == ANSWER_KEY_MATRIC:
            continue
        if matric == UNREAD_MATRIC or matric in seen:
            if matric in seen:
                logging.warning(f"Duplicate matriculation number {matric} on page {s.page_index + 1}")
            matric = str(fallback)
            fallback -= 1
        seen.add(matric)

        row = {"Matriculation number": matric}
        for q in range(1, nq + 1):
            selected = set(s.answers.get(q, []))
            correct = answer_key.correct_for(q)
            ncorrect, nincorrect = question_marks(selected, correct)
            row[f"Question{q}NumCorrect"] = ncorrect
            row[f"Question{q}NumIncorrect"] = nincorrect
            row[f"Question{q}Answer"] = options_to_letters(selected)
            row[f"Question{q}Weight"] = answer_key.weight_for(q)
        if strategy is not None:
            row["Total"] = score_scan(s, answer_key, strategy, options, num_options)
        rows.append(row)

    return pd.DataFrame(rows)
