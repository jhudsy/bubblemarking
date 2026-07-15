"""Partial credit but zero if any incorrect options were selected.

Award proportional credit for each correct option selected, but if the
student selected any incorrect options, the question scores 0.0.
"""

NAME = "Partial credit (no incorrects)"
DESCRIPTION = "Proportional credit only when no incorrect options selected."
OPTIONS = {}


def score(selected, correct, weight, num_options, **opts):
    selected = set(selected)
    correct = set(correct)
    if not correct:
        return 0.0
    # If any selected option is incorrect, score is zero.
    if selected - correct:
        return 0.0
    per_correct = float(weight) / len(correct)
    return per_correct * len(selected & correct)
