"""Partial credit: per correct option selected, award an equal share of the
question's weight; subtract a configurable fraction per wrong option."""

NAME = "Partial credit"
DESCRIPTION = "Proportional credit for correct options minus a fraction per wrong option."
OPTIONS = {
    "wrong_penalty_fraction": {
        "type": float,
        "default": 1.0,
        "label": "Penalty per wrong (fraction of per-correct value)",
        "tooltip": "1.0 means each wrong cancels one correct. 0 disables in-question negatives.",
    },
    "floor_at_zero": {
        "type": bool,
        "default": True,
        "label": "Floor question score at zero",
    },
}


def score(selected, correct, weight, num_options,
          wrong_penalty_fraction=1.0, floor_at_zero=True, **opts):
    selected = set(selected)
    correct = set(correct)
    if not correct:
        return 0.0
    per_correct = float(weight) / len(correct)
    raw = (per_correct * len(selected & correct)
           - float(wrong_penalty_fraction) * per_correct * len(selected - correct))
    return max(0.0, raw) if floor_at_zero else raw
