from rulebook.loader import load_rulebook

RULES_PATH = "rulebook/rules.yaml"


def test_load_baseline():
    rb = load_rulebook(RULES_PATH)
    assert rb.profile == "D1_H4"
    assert rb.setup.trap_mode == "wick"
    assert rb.exit.model == "next_level"


def test_profile_override():
    rb = load_rulebook(RULES_PATH, profile="H4_H1")
    assert rb.profile == "H4_H1"


def test_variant_overrides_nested_fields_without_losing_siblings():
    rb = load_rulebook(RULES_PATH, variant="grid_close_AB_ladder")
    assert rb.setup.trap_mode == "close"
    assert rb.setup.accepted_grades == ["A", "B"]
    assert rb.exit.model == "ladder"
    # untouched sibling fields on the same sub-model survive the merge
    assert rb.setup.confirm_window == 3
    assert rb.zone.max_base == 6


def test_unknown_variant_raises():
    import pytest
    with pytest.raises(KeyError):
        load_rulebook(RULES_PATH, variant="does_not_exist")
