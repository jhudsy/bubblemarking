"""Per-strategy scoring rules + the loader/coercion helpers."""
import textwrap

import pytest

from bubblemarking import scoring
from bubblemarking.scoring import (
    all_or_nothing,
    coerce_options,
    default_options,
    list_builtins,
    load_strategy_from_file,
    negative_marking,
    partial_credit,
)


# ----------------------------------------------------------- builtins
def test_list_builtins_returns_three():
    mods = list(list_builtins())
    names = [m.NAME for m in mods]
    assert len(mods) == 3
    assert all(callable(m.score) for m in mods)
    assert all(hasattr(m, "DESCRIPTION") for m in mods)
    assert all(hasattr(m, "OPTIONS") for m in mods)
    # No duplicate names — they're shown in a combo box.
    assert len(set(names)) == 3


def test_default_options():
    opts = default_options(partial_credit)
    assert opts == {"wrong_penalty_fraction": 1.0, "floor_at_zero": True}
    assert default_options(all_or_nothing) == {}


# ----------------------------------------------------------- all_or_nothing
class TestAllOrNothing:
    def test_exact_match(self):
        assert all_or_nothing.score({0, 1}, {0, 1}, 2.0, 5) == 2.0

    def test_partial_correct_scores_zero(self):
        assert all_or_nothing.score({0}, {0, 1}, 2.0, 5) == 0.0

    def test_extra_wrong_scores_zero(self):
        assert all_or_nothing.score({0, 1, 2}, {0, 1}, 2.0, 5) == 0.0

    def test_blank_scores_zero(self):
        assert all_or_nothing.score(set(), {0}, 1.0, 5) == 0.0


# ----------------------------------------------------------- partial_credit
class TestPartialCredit:
    def test_perfect(self):
        # weight 2, 2 correct options, both selected → full marks
        assert partial_credit.score({0, 1}, {0, 1}, 2.0, 5,
                                    wrong_penalty_fraction=1.0,
                                    floor_at_zero=True) == 2.0

    def test_half_correct(self):
        # 1 of 2 correct selected, 0 wrong → weight/2
        assert partial_credit.score({0}, {0, 1}, 2.0, 5,
                                    wrong_penalty_fraction=1.0,
                                    floor_at_zero=True) == 1.0

    def test_one_correct_one_wrong_floors_at_zero(self):
        # 1 right, 1 wrong → 1 - 1 = 0
        assert partial_credit.score({0, 4}, {0, 1}, 2.0, 5,
                                    wrong_penalty_fraction=1.0,
                                    floor_at_zero=True) == 0.0

    def test_negative_when_floor_disabled(self):
        # 0 right, 1 wrong → -1
        s = partial_credit.score({4}, {0, 1}, 2.0, 5,
                                 wrong_penalty_fraction=1.0,
                                 floor_at_zero=False)
        assert s == -1.0

    def test_lower_penalty_keeps_credit(self):
        # 1 right, 1 wrong, penalty fraction 0.5 → 1 - 0.5 = 0.5
        assert partial_credit.score({0, 4}, {0, 1}, 2.0, 5,
                                    wrong_penalty_fraction=0.5,
                                    floor_at_zero=True) == 0.5

    def test_no_correct_options(self):
        # Edge: a question with no correct answer in the key.
        assert partial_credit.score({0}, set(), 1.0, 5) == 0.0


# ----------------------------------------------------------- negative_marking
class TestNegativeMarking:
    def test_exact_match_full_weight(self):
        assert negative_marking.score({0, 1}, {0, 1}, 1.0, 5,
                                      penalty_per_wrong=0.25) == 1.0

    def test_blank_scores_zero(self):
        assert negative_marking.score(set(), {0}, 1.0, 5,
                                      penalty_per_wrong=0.25) == 0.0

    def test_one_wrong_penalty(self):
        assert negative_marking.score({4}, {0}, 1.0, 5,
                                      penalty_per_wrong=0.25) == -0.25

    def test_two_wrong_two_penalties(self):
        assert negative_marking.score({3, 4}, {0}, 1.0, 5,
                                      penalty_per_wrong=0.25) == -0.5

    def test_penalise_partial_correct_off_overlap_immune(self):
        # Selected has overlap with correct + a wrong.
        s = negative_marking.score({0, 4}, {0, 1}, 1.0, 5,
                                   penalty_per_wrong=0.25,
                                   penalise_partial_correct=False)
        assert s == 0.0

    def test_penalise_partial_correct_on_still_penalises(self):
        s = negative_marking.score({0, 4}, {0, 1}, 1.0, 5,
                                   penalty_per_wrong=0.25,
                                   penalise_partial_correct=True)
        assert s == -0.25  # one wrong


# ----------------------------------------------------------- coercion
def test_coerce_options_falls_back_to_defaults_on_bad_input():
    raw = {"wrong_penalty_fraction": "not-a-float", "floor_at_zero": "yes"}
    out = coerce_options(partial_credit, raw)
    assert out["wrong_penalty_fraction"] == 1.0  # default
    assert out["floor_at_zero"] is True  # "yes" → True


def test_coerce_options_missing_keys_use_defaults():
    out = coerce_options(partial_credit, {})
    assert out == {"wrong_penalty_fraction": 1.0, "floor_at_zero": True}


def test_coerce_options_string_to_bool():
    for s, expected in [("true", True), ("0", False), ("on", True), ("off", False)]:
        out = coerce_options(partial_credit, {"floor_at_zero": s})
        assert out["floor_at_zero"] is expected


# ----------------------------------------------------------- custom loader
def test_load_strategy_from_file(tmp_path):
    p = tmp_path / "custom.py"
    p.write_text(textwrap.dedent("""
        NAME = "test strategy"
        DESCRIPTION = "double weight if any correct selected"
        OPTIONS = {}
        def score(selected, correct, weight, num_options, **opts):
            return 2.0 * weight if (set(selected) & set(correct)) else 0.0
    """))
    mod = load_strategy_from_file(str(p))
    assert mod.NAME == "test strategy"
    assert mod.score({0}, {0, 1}, 1.5, 5) == 3.0
    assert mod.score(set(), {0}, 1.5, 5) == 0.0


def test_load_strategy_rejects_module_without_score(tmp_path):
    p = tmp_path / "bad.py"
    p.write_text("NAME = 'no score function here'\n")
    with pytest.raises(ValueError, match="score"):
        load_strategy_from_file(str(p))


def test_load_strategy_supplies_defaults(tmp_path):
    p = tmp_path / "minimal.py"
    p.write_text("def score(selected, correct, weight, num_options, **opts): return weight\n")
    mod = load_strategy_from_file(str(p))
    assert hasattr(mod, "NAME")
    assert hasattr(mod, "DESCRIPTION")
    assert mod.OPTIONS == {}
