"""Exact weapon and general-slot capacity regression tests for stock hulls."""
from __future__ import annotations

import pytest

from stars_ai.design_legality import ComponentCategory, ComponentRef, validate_design
from stars_ai.starsapi_items import stock_hulls


ENGINE = int(ComponentCategory.ENGINE)
BEAM = int(ComponentCategory.BEAM_WEAPON)
BOMB = int(ComponentCategory.BOMB)


def _empty_with_engine(hull_id: int) -> list[ComponentRef]:
    hull = stock_hulls()[hull_id]
    slots = [ComponentRef(0, 0, 0) for _ in hull.slots]
    if not hull.is_starbase:
        slots[0] = ComponentRef(ENGINE, 0, hull.engine_count)
    return slots


def _max_category_design(hull_id: int, category: int) -> tuple[list[ComponentRef], list[int]]:
    hull = stock_hulls()[hull_id]
    slots = _empty_with_engine(hull_id)
    indices = []
    for slot in hull.slots:
        if slot.allows(category):
            slots[slot.index] = ComponentRef(category, 0, slot.capacity)
            indices.append(slot.index)
    return slots, indices


@pytest.mark.parametrize(
    ("hull_id", "category", "expected_count"),
    [
        (6, BEAM, 3),       # Destroyer
        (7, BEAM, 6),       # Cruiser
        (9, BEAM, 20),      # Battleship
        (17, BOMB, 8),      # B-17 Bomber
        (19, BOMB, 16),     # B-52 Bomber
    ],
)
def test_stock_hull_weapon_capacity_accepts_exact_limit_and_rejects_one_more(hull_id, category, expected_count):
    slots, indices = _max_category_design(hull_id, category)
    assert sum(slots[index].count for index in indices) == expected_count
    assert validate_design(hull_id, slots).ok

    overflow = list(slots)
    index = indices[0]
    overflow[index] = ComponentRef(category, 0, overflow[index].count + 1)
    result = validate_design(hull_id, overflow)
    assert not result.ok
    assert any(issue.code == "too_many_items" and issue.slot_index == index for issue in result.issues)


def test_nubian_has_twelve_general_groups_of_three_and_no_thirteenth_group():
    nubian = stock_hulls()[29]
    slots, indices = _max_category_design(29, BEAM)
    assert len(indices) == 12
    assert all(slots[index].count == 3 for index in indices)
    assert sum(slots[index].count for index in indices) == 36
    assert validate_design(29, slots).ok

    # A thirteenth serialized component group is illegal by slot count; an
    # extra component in any existing group is illegal by that group's cap.
    extra_group = slots + [ComponentRef(BEAM, 0, 3)]
    assert any(issue.code == "slot_count" for issue in validate_design(29, extra_group).issues)
    overflow = list(slots)
    overflow[indices[-1]] = ComponentRef(BEAM, 0, 4)
    assert any(issue.code == "too_many_items" for issue in validate_design(29, overflow).issues)
