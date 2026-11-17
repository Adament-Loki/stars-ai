"""DesignBlock armor is hull armor plus fitted armor, not the hull alone."""

from pathlib import Path

from stars_ai.native.player_state import PlayerState
from stars_ai.starsapi_items import ARMOR, MECHANICAL, SHIELD, design_armor_value, stock_hulls


def _slot(category, item_id, count):
    return {"category": category, "item_id": item_id, "count": count}


def test_crobmnium_adds_to_destroyer_hull():
    # Captured Stalwart Defender: Destroyer 200 + two Crobmnium (75) = 350.
    armor = design_armor_value(200, [_slot(ARMOR, 1, 2)])
    assert armor == 350


def test_croby_and_multi_cargo_are_bonus_armor():
    # Medium Freighter 50 + one Croby Sharmor = 115.
    assert design_armor_value(50, [_slot(SHIELD, 3, 1)]) == 115
    # Large Freighter 150 + two Langston (65) + two Multi-Cargo Pods (50) = 380.
    slots = [_slot(SHIELD, 6, 2), _slot(MECHANICAL, 4, 2)]
    assert design_armor_value(150, slots) == 380


def test_regenerating_shields_halves_armor_components_only():
    # Two Organic Armor are 350 DP. The sum is halved to 175; Croby's 65 is not.
    slots = [_slot(ARMOR, 4, 2), _slot(SHIELD, 3, 1)]
    assert design_armor_value(450, slots, regenerating_shields=True) == 450 + 175 + 65
    assert design_armor_value(450, slots, regenerating_shields=False) == 450 + 350 + 65


def test_captured_ship_designs_match_the_formula():
    path = Path("tests/TestResource/sandboxes/sandbox-native-reference/GAME.m1")
    if not path.is_file():
        return
    state = PlayerState.from_files(path)
    hulls = stock_hulls()
    checked = 0
    for design in state.designs:
        if not design.is_full_design or design.is_starbase or not design.armor:
            continue
        hull = hulls.get(design.hull_id)
        if hull is None:
            continue
        slots = [
            {"category": slot.category, "item_id": slot.item_id, "count": slot.count}
            for slot in design.slots
        ]
        plain = design_armor_value(hull.armor, slots)
        halved = design_armor_value(hull.armor, slots, regenerating_shields=True)
        assert design.armor in {plain, halved}, (design.name, design.armor, plain, halved)
        checked += 1
    assert checked > 0
