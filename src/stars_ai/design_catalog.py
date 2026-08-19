from __future__ import annotations

from .design_legality import CATEGORY_NAMES, HULL_RULES


def category_names_for_mask(mask: int) -> list[str]:
    return [name for bit, name in CATEGORY_NAMES.items() if mask & bit]


def hull_catalog() -> list[dict]:
    out = []
    for hull_id in sorted(HULL_RULES):
        hull = HULL_RULES[hull_id]
        out.append({
            "hull_id": hull.hull_id,
            "name": hull.name,
            "kind": "starbase" if hull.is_starbase else "ship",
            "slot_count": len(hull.slots),
            "slots": [
                {
                    "index": i,
                    "capacity": slot.max_count,
                    "allowed_mask": slot.allowed_categories,
                    "allowed_categories": category_names_for_mask(slot.allowed_categories),
                    "required": slot.required,
                }
                for i, slot in enumerate(hull.slots)
            ],
        })
    return out
