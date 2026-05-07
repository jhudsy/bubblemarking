"""Negative marking: full weight on an exact match, otherwise a flat penalty
per wrong selection. A blank answer scores zero."""

NAME = "All-or-nothing with negative marking"
DESCRIPTION = "Full weight on exact match; flat penalty per wrong selection. Blank scores zero."
OPTIONS = {
    "penalty_per_wrong": {
        "type": float,
        "default": 0.25,
        "label": "Points subtracted per wrong selection",
    },
    "penalise_partial_correct": {
        "type": bool,
        "default": True,
        "label": "Apply penalty even when some correct options were also selected",
        "tooltip": "If unticked, only fully-wrong (no overlap) answers are penalised.",
    },
}


def score(selected, correct, weight, num_options,
          penalty_per_wrong=0.25, penalise_partial_correct=True, **opts):
    selected = set(selected)
    correct = set(correct)
    if selected == correct:
        return float(weight)
    if not selected:
        return 0.0
    wrong = len(selected - correct)
    if wrong == 0:
        return 0.0
    if not penalise_partial_correct and (selected & correct):
        return 0.0
    return -float(penalty_per_wrong) * wrong
