"""All-or-nothing: full weight if and only if the selection matches the key
exactly. No partial credit, no negative marking."""

NAME = "All or nothing"
DESCRIPTION = "Full weight only when the selection matches the key exactly."
OPTIONS = {}


def score(selected, correct, weight, num_options, **opts):
    return float(weight) if set(selected) == set(correct) else 0.0
