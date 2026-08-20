from __future__ import annotations

from dataclasses import dataclass
from enum import IntFlag
from typing import Iterable, Sequence


class ComponentCategory(IntFlag):
    EMPTY = 0
    ENGINE = 0x0001
    SCANNER = 0x0002
    SHIELD = 0x0004
    ARMOR = 0x0008
    BEAM_WEAPON = 0x0010
    TORPEDO = 0x0020
    BOMB = 0x0040
    MINING_ROBOT = 0x0080
    MINE_LAYER = 0x0100
    ORBITAL = 0x0200
    PLANETARY = 0x0400
    ELECTRICAL = 0x0800
    MECHANICAL = 0x1000


CATEGORY_NAMES = {
    int(ComponentCategory.ENGINE): "Engine",
    int(ComponentCategory.SCANNER): "Scanner",
    int(ComponentCategory.SHIELD): "Shield",
    int(ComponentCategory.ARMOR): "Armor",
    int(ComponentCategory.BEAM_WEAPON): "Beam Weapon",
    int(ComponentCategory.TORPEDO): "Torpedo",
    int(ComponentCategory.BOMB): "Bomb",
    int(ComponentCategory.MINING_ROBOT): "Mining Robot",
    int(ComponentCategory.MINE_LAYER): "Mine Layer",
    int(ComponentCategory.ORBITAL): "Orbital",
    int(ComponentCategory.PLANETARY): "Planetary",
    int(ComponentCategory.ELECTRICAL): "Electrical",
    int(ComponentCategory.MECHANICAL): "Mechanical",
}


@dataclass(frozen=True)
class ComponentRef:
    category: int
    item_id: int
    count: int = 1


@dataclass(frozen=True)
class HullSlotRule:
    allowed_categories: int
    max_count: int
    required: bool = False
    label: str = ""

    def allows(self, category: int) -> bool:
        return category == 0 or bool(self.allowed_categories & category)


@dataclass(frozen=True)
class HullRule:
    hull_id: int
    name: str
    slots: tuple[HullSlotRule, ...]
    is_starbase: bool = False


@dataclass(frozen=True)
class ValidationIssue:
    slot_index: int | None
    code: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    issues: tuple[ValidationIssue, ...]

    def raise_for_error(self) -> None:
        if self.ok:
            return
        raise ValueError("; ".join(issue.message for issue in self.issues))


# Full stock Stars! legality matrix. The native MOD data stores each hull slot as
# an allowed-category bitmask plus maximum component count. Combined masks are
# preserved exactly rather than narrowed to currently observed designs.
from .standard_hulls import STANDARD_HULL_SLOTS

HULL_RULES: dict[int, HullRule] = {
    hull_id: HullRule(
        hull_id=hull_id,
        name=name,
        slots=tuple(
            HullSlotRule(
                allowed_categories=mask,
                max_count=capacity,
                required=(not is_starbase and idx == 0 and mask == int(ComponentCategory.ENGINE)),
                label=("Engine" if (not is_starbase and idx == 0) else f"Slot {idx}"),
            )
            for idx, (capacity, mask) in enumerate(slot_defs)
        ),
        is_starbase=is_starbase,
    )
    for hull_id, name, is_starbase, slot_defs in STANDARD_HULL_SLOTS
}


def _category_label(category: int) -> str:
    if category == 0:
        return "Empty"
    return CATEGORY_NAMES.get(category, f"Unknown category 0x{category:04x}")


def available_components_from_designs(designs: Iterable[Sequence[ComponentRef]]) -> set[tuple[int, int]]:
    """Return conservative availability from components already visible on legal designs.

    This does not claim to enumerate every researched component; it provides a safe
    lower bound. A future tech-table reader can replace/augment this set.
    """
    available: set[tuple[int, int]] = set()
    for slots in designs:
        for comp in slots:
            if comp.category and comp.count:
                available.add((comp.category, comp.item_id))
    return available


def validate_design(
    hull_id: int,
    components: Sequence[ComponentRef],
    *,
    available_components: set[tuple[int, int]] | None = None,
    hull_rules: dict[int, HullRule] | None = None,
) -> ValidationResult:
    rules = HULL_RULES if hull_rules is None else hull_rules
    hull = rules.get(hull_id)
    issues: list[ValidationIssue] = []

    if hull is None:
        issues.append(ValidationIssue(None, "unknown_hull", f"No legality profile is loaded for hull ID {hull_id}."))
        return ValidationResult(False, tuple(issues))

    if len(components) != len(hull.slots):
        issues.append(
            ValidationIssue(
                None,
                "slot_count",
                f"{hull.name} hull requires {len(hull.slots)} equipment slots; design supplied {len(components)}.",
            )
        )
        return ValidationResult(False, tuple(issues))

    for idx, (comp, slot) in enumerate(zip(components, hull.slots)):
        if comp.count < 0:
            issues.append(ValidationIssue(idx, "negative_count", f"Slot {idx} has negative quantity {comp.count}."))
            continue

        empty = comp.category == 0 or comp.count == 0
        if empty:
            if slot.required:
                issues.append(ValidationIssue(idx, "required_empty", f"Slot {idx} ({slot.label}) cannot be empty."))
            continue

        if not slot.allows(comp.category):
            issues.append(
                ValidationIssue(
                    idx,
                    "illegal_category",
                    f"Slot {idx} ({slot.label}) does not allow {_category_label(comp.category)}.",
                )
            )

        if comp.count > slot.max_count:
            issues.append(
                ValidationIssue(
                    idx,
                    "too_many_items",
                    f"Slot {idx} ({slot.label}) allows at most {slot.max_count} item(s); requested {comp.count}.",
                )
            )

        # Ship engines are an all-or-nothing hull requirement. A hull with
        # N engine positions must carry exactly N copies of one engine type.
        # ComponentRef is one category/item/count tuple, so this also prevents
        # mixed-engine semantic designs.
        if (
            slot.required
            and comp.category == int(ComponentCategory.ENGINE)
            and int(comp.count) != int(slot.max_count)
        ):
            issues.append(
                ValidationIssue(
                    idx,
                    "engine_count_mismatch",
                    f"Slot {idx} ({slot.label}) requires exactly {slot.max_count} identical engines; requested {comp.count}.",
                )
            )

        if available_components is not None and (comp.category, comp.item_id) not in available_components:
            issues.append(
                ValidationIssue(
                    idx,
                    "component_not_known_available",
                    f"Slot {idx} uses {_category_label(comp.category)} item {comp.item_id}, which is not in the player's known-available component set.",
                )
            )

    return ValidationResult(not issues, tuple(issues))


def decode_design_components(design_data: bytes) -> tuple[int, tuple[ComponentRef, ...]]:
    """Decode hull ID and equipment slots from a decrypted Stars! DesignBlock payload."""
    if len(design_data) < 17:
        raise ValueError("Design block is too short")
    hull_id = design_data[2]
    slot_count = design_data[6]
    end = 17 + 4 * slot_count
    if len(design_data) < end:
        raise ValueError("Design block does not contain all declared component slots")
    slots: list[ComponentRef] = []
    for idx in range(slot_count):
        p = 17 + 4 * idx
        slots.append(
            ComponentRef(
                category=int.from_bytes(design_data[p : p + 2], "little"),
                item_id=design_data[p + 2],
                count=design_data[p + 3],
            )
        )
    return hull_id, tuple(slots)
