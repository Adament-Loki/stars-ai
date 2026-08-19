"""Parser for stock/custom Stars! MOD databases.

The parser follows the field indexing used by StarsAPI Items.java. This allows the
AI to ingest the canonical UNEDITED.MOD or a compatible custom MOD file and derive
component tech requirements and hull-slot rules instead of hard-coding guesses.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .design_legality import ComponentCategory, HullRule, HullSlotRule

TECH_FIELDS = ("energy", "weapons", "propulsion", "construction", "electronics", "biotechnology")

# MOD category -> native design category mask.
MOD_CATEGORY_TO_MASK = {
    1: int(ComponentCategory.ORBITAL),
    2: int(ComponentCategory.BEAM_WEAPON),
    3: int(ComponentCategory.TORPEDO),
    4: int(ComponentCategory.BOMB),
    # 5 = terraforming (planetary production item, not ship equipment)
    6: int(ComponentCategory.PLANETARY),
    7: int(ComponentCategory.MINING_ROBOT),
    8: int(ComponentCategory.MINE_LAYER),
    9: int(ComponentCategory.MECHANICAL),
    10: int(ComponentCategory.ELECTRICAL),
    11: int(ComponentCategory.SHIELD),
    12: int(ComponentCategory.SCANNER),
    13: int(ComponentCategory.ARMOR),
    14: int(ComponentCategory.ENGINE),
}

@dataclass(frozen=True)
class TechLevels:
    energy: int = 0
    weapons: int = 0
    propulsion: int = 0
    construction: int = 0
    electronics: int = 0
    biotechnology: int = 0

    def meets(self, required: tuple[int, int, int, int, int, int]) -> bool:
        have = tuple(getattr(self, f) for f in TECH_FIELDS)
        return all(h >= r for h, r in zip(have, required))

@dataclass(frozen=True)
class ComponentSpec:
    category: int
    item_id: int  # native 0-based DesignBlock item id
    name: str
    tech_required: tuple[int, int, int, int, int, int]
    mass: int
    resource_cost: int
    ironium: int
    boranium: int
    germanium: int

@dataclass(frozen=True)
class ModDatabase:
    components: dict[tuple[int, int], ComponentSpec]
    hulls: dict[int, HullRule]

    def available_components(self, tech: TechLevels) -> set[tuple[int, int]]:
        return {key for key, spec in self.components.items() if tech.meets(spec.tech_required)}

    def component(self, category: int, item_id: int) -> ComponentSpec | None:
        return self.components.get((category, item_id))


def _num(parts: list[str], index: int) -> int:
    if index >= len(parts) or parts[index] == "":
        return 0
    return int(parts[index])


def parse_mod_text(text: str) -> ModDatabase:
    components: dict[tuple[int, int], ComponentSpec] = {}
    hulls: dict[int, HullRule] = {}
    for parts in csv.reader(text.splitlines()):
        if len(parts) < 4:
            continue
        mod_cat = int(parts[0])
        row_index = int(parts[1])
        name = parts[2]
        nums = [int(x) if x else 0 for x in parts[3:]]

        if mod_cat in MOD_CATEGORY_TO_MASK:
            mask = MOD_CATEGORY_TO_MASK[mod_cat]
            native_item_id = row_index - 1
            required = tuple(nums[1:7])
            # Matches StarsAPI Items.java constants: MASS_INDEX=7. Costs immediately
            # follow mass in the stock MOD layout.
            components[(mask, native_item_id)] = ComponentSpec(
                category=mask,
                item_id=native_item_id,
                name=name,
                tech_required=required,  # type: ignore[arg-type]
                mass=nums[7] if len(nums) > 7 else 0,
                resource_cost=nums[8] if len(nums) > 8 else 0,
                ironium=nums[9] if len(nums) > 9 else 0,
                boranium=nums[10] if len(nums) > 10 else 0,
                germanium=nums[11] if len(nums) > 11 else 0,
            )
            continue

        if mod_cat in (15, 16):
            hull_id = nums[0]
            # Stock/custom MOD layouts publish a slot count field near the end.
            # Known stock hull IDs also have an authoritative fallback table.
            from .standard_hulls import STANDARD_SLOT_COUNTS
            candidates = []
            for idx in (48, 46):
                if idx < len(nums) and 1 <= nums[idx] <= 16:
                    candidates.append(nums[idx])
            slot_count = STANDARD_SLOT_COUNTS.get(hull_id, candidates[0] if candidates else 0)
            if not slot_count:
                raise ValueError(f"Cannot determine slot count for hull {name!r} ({hull_id})")
            slots: list[HullSlotRule] = []
            if mod_cat == 15:
                # Slot zero is always the ship engine slot.
                slots.append(HullSlotRule(int(ComponentCategory.ENGINE), nums[17], True, "Engine"))
                for i in range(1, slot_count):
                    allowed = nums[16 + 2*i]
                    capacity = nums[17 + 2*i]
                    slots.append(HullSlotRule(allowed, capacity, False, f"Slot {i}"))
            else:
                for i in range(slot_count):
                    allowed = nums[16 + 2*i]
                    capacity = nums[17 + 2*i]
                    slots.append(HullSlotRule(allowed, capacity, False, f"Slot {i}"))
            hulls[hull_id] = HullRule(hull_id, name, tuple(slots), is_starbase=(mod_cat == 16))

    return ModDatabase(components=components, hulls=hulls)


def parse_mod_file(path: str | Path) -> ModDatabase:
    return parse_mod_text(Path(path).read_text(encoding="latin-1"))


def validate_design_against_mod(
    hull_id: int,
    components,
    database: ModDatabase,
    tech: TechLevels,
):
    """Validate physical slot legality and researched component availability."""
    from .design_legality import validate_design
    return validate_design(
        hull_id,
        components,
        hull_rules=database.hulls,
        available_components=database.available_components(tech),
    )


def legal_available_components_for_slot(
    hull_id: int,
    slot_index: int,
    database: ModDatabase,
    tech: TechLevels,
) -> list[ComponentSpec]:
    """Return components that are BOTH researched and legal for one hull slot."""
    hull = database.hulls.get(hull_id)
    if hull is None:
        raise ValueError(f"Unknown hull ID {hull_id}")
    if not (0 <= slot_index < len(hull.slots)):
        raise IndexError(f"Hull {hull.name} has {len(hull.slots)} slots")
    slot = hull.slots[slot_index]
    result = []
    for spec in database.components.values():
        if not slot.allows(spec.category):
            continue
        if not tech.meets(spec.tech_required):
            continue
        result.append(spec)
    return sorted(result, key=lambda s: (s.category, s.item_id, s.name))


def availability_snapshot(database: ModDatabase, tech: TechLevels) -> list[dict]:
    """Machine-readable complete researched component inventory."""
    out = []
    for spec in sorted(database.components.values(), key=lambda s: (s.category, s.item_id)):
        if tech.meets(spec.tech_required):
            out.append({
                "category": spec.category,
                "item_id": spec.item_id,
                "name": spec.name,
                "tech_required": list(spec.tech_required),
                "mass": spec.mass,
                "resource_cost": spec.resource_cost,
                "ironium": spec.ironium,
                "boranium": spec.boranium,
                "germanium": spec.germanium,
            })
    return out
