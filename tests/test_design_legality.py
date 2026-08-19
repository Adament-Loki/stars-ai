from stars_ai.design_legality import (
    ComponentCategory,
    ComponentRef,
    HULL_RULES,
    available_components_from_designs,
    validate_design,
)
from stars_ai.standard_hulls import STANDARD_SLOT_COUNTS


def armed_probe():
    return (
        ComponentRef(int(ComponentCategory.ENGINE), 3, 1),
        ComponentRef(int(ComponentCategory.SCANNER), 1, 1),
        ComponentRef(int(ComponentCategory.BEAM_WEAPON), 1, 1),
    )


def long_range_scout():
    return (
        ComponentRef(int(ComponentCategory.ENGINE), 3, 1),
        ComponentRef(int(ComponentCategory.SCANNER), 1, 1),
        ComponentRef(int(ComponentCategory.MECHANICAL), 5, 1),
    )


def test_all_37_standard_hulls_are_loaded():
    assert len(HULL_RULES) == 37
    assert set(HULL_RULES) == set(range(37))
    assert sum(1 for h in HULL_RULES.values() if h.is_starbase) == 5
    assert sum(1 for h in HULL_RULES.values() if not h.is_starbase) == 32


def test_all_slot_counts_match_stock_layout():
    for hull_id, expected in STANDARD_SLOT_COUNTS.items():
        assert len(HULL_RULES[hull_id].slots) == expected


def test_scout_has_full_native_general_slot_mask():
    scout = HULL_RULES[4]
    assert [s.max_count for s in scout.slots] == [1, 1, 1]
    assert scout.slots[0].allowed_categories == int(ComponentCategory.ENGINE)
    assert scout.slots[1].allowed_categories == int(ComponentCategory.SCANNER)
    # Native mask 6462 allows Scanner, Shield, Armor, Beam, Torpedo, Mine Layer,
    # Electrical and Mechanical equipment.
    assert scout.slots[2].allowed_categories == 6462
    for category in (
        ComponentCategory.SCANNER,
        ComponentCategory.SHIELD,
        ComponentCategory.ARMOR,
        ComponentCategory.BEAM_WEAPON,
        ComponentCategory.TORPEDO,
        ComponentCategory.MINE_LAYER,
        ComponentCategory.ELECTRICAL,
        ComponentCategory.MECHANICAL,
    ):
        assert scout.slots[2].allows(int(category))


def test_known_scout_designs_validate():
    available = available_components_from_designs([armed_probe(), long_range_scout()])
    assert validate_design(4, armed_probe(), available_components=available).ok
    assert validate_design(4, long_range_scout(), available_components=available).ok


def test_wrong_category_in_scanner_slot_is_rejected():
    available = available_components_from_designs([armed_probe(), long_range_scout()])
    bad = list(armed_probe())
    bad[1] = ComponentRef(int(ComponentCategory.MECHANICAL), 5, 1)
    result = validate_design(4, bad, available_components=available)
    assert not result.ok
    assert any(issue.code == "illegal_category" and issue.slot_index == 1 for issue in result.issues)


def test_unknown_component_is_rejected_even_if_category_fits():
    available = available_components_from_designs([armed_probe(), long_range_scout()])
    bad = list(armed_probe())
    bad[1] = ComponentRef(int(ComponentCategory.SCANNER), 99, 1)
    result = validate_design(4, bad, available_components=available)
    assert not result.ok
    assert any(issue.code == "component_not_known_available" for issue in result.issues)


def test_capacity_is_enforced_on_heavy_hull():
    battleship = HULL_RULES[9]
    assert battleship.slots[0].max_count == 4
    comps = [ComponentRef(0, 0, 0) for _ in battleship.slots]
    comps[0] = ComponentRef(int(ComponentCategory.ENGINE), 0, 5)
    result = validate_design(9, comps)
    assert not result.ok
    assert any(i.code == "too_many_items" and i.slot_index == 0 for i in result.issues)


def test_starbase_does_not_require_engine_slot():
    fort = HULL_RULES[32]
    assert fort.is_starbase
    assert not any(slot.required for slot in fort.slots)
