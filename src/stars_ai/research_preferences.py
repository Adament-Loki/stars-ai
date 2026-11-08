"""Preference points for choosing the next research goal.

Each candidate already has an unlock score. These points say how much that
unlock is worth to this race. The planner adds them up and researches the
highest total. Nothing here forces a field order.
"""
from __future__ import annotations

from typing import Any

from .starsapi_items import BEAM, TORPEDO, component_spec


# Added after the phase weight, so a useful unlock can still outrank a
# category the opening would otherwise ignore.
AR_ENERGY_AT_ZERO = 280.0
BET_ENERGY_POINTS = 36.0
PLAIN_ENERGY_PENALTY = -14.0
WEAPON_PARITY_POINTS = 180.0
WEAPON_AHEAD_PENALTY = -120.0
FUEL_MIZER_POINTS = 48.0
PROPULSION_NICHE_PENALTY = -16.0
BIOTECH_NICHE_PENALTY = -12.0
BIOTECH_GROWTH_POINTS = 8.0
ELECTRONICS_FLOOR_POINTS = 22.0
HULL_POINTS = {
    "Cruiser": 120.0,
    "Galleon": 110.0,
    "Battleship": 100.0,
    "Nubian": 70.0,
    "Death Star": 100.0,
}
FIELDS = ("energy", "weapons", "propulsion", "construction", "electronics", "biotechnology")


def preference_points(state: Any, capability: Any) -> tuple[float, tuple[tuple[str, float], ...]]:
    """Return ``(total, ((name, points), ...))`` for one research candidate."""
    remaining = capability.remaining(state.tech)
    if not remaining:
        return 0.0, ()
    primary = max(remaining, key=lambda field: (remaining[field], -FIELDS.index(field)))
    tags = set(capability.tags or ())
    rows: list[tuple[str, float]] = []

    def add(name: str, points: float) -> None:
        if points:
            rows.append((name, float(points)))

    race_native = (getattr(state.race, "native", {}) or {})
    lrts = {str(item).upper() for item in (race_native.get("lrts") or [])}
    alternate = _is_alternate_reality(state)
    energy = int(getattr(state.tech, "energy", 0) or 0)
    weapons = int(getattr(state.tech, "weapons", 0) or 0)
    propulsion = int(getattr(state.tech, "propulsion", 0) or 0)

    if primary == "energy" and alternate:
        add("ar_energy", AR_ENERGY_AT_ZERO / (1 + energy))
    elif primary == "energy" and "BET" in lrts:
        add("bleeding_edge_energy", BET_ENERGY_POINTS)
    elif primary == "energy":
        add("energy_plain", PLAIN_ENERGY_PENALTY)

    if "combat_hull" in tags and capability.name in HULL_POINTS:
        label = "death_star" if capability.name == "Death Star" else (
            "hull_" + capability.name.casefold().replace(" ", "_")
        )
        points = HULL_POINTS[capability.name]
        # Alternate Reality's construction payoff is the Death Star. Nearer
        # hulls stay on the menu, at a lower point value, so the formula can
        # still pick a one-level Cruiser when the Death Star is far away.
        if alternate and capability.name != "Death Star":
            points = min(points, 40.0)
        add(label, points)

    enemy_weapons = observed_enemy_weapon_level(state)
    if primary == "weapons" and enemy_weapons is not None:
        # Stay at the enemy fleet's weapon tech, or one level above it.
        if weapons < enemy_weapons + 1:
            gap = (enemy_weapons + 1) - weapons
            add("weapon_parity", WEAPON_PARITY_POINTS * min(1.0, gap / 4.0))
        else:
            add("weapon_ahead", WEAPON_AHEAD_PENALTY)

    fuel_mizer = (
        primary == "propulsion"
        and "IFE" in lrts
        and propulsion < 2
        and (
            "fuel_mizer" in str(capability.capability_id)
            or "engine" in tags
        )
    )
    if fuel_mizer:
        add("fuel_mizer", FUEL_MIZER_POINTS)
    elif primary == "propulsion":
        add("propulsion_niche", PROPULSION_NICHE_PENALTY)

    if capability.category == "terraforming" or primary == "biotechnology":
        if "growth_unlock" in tags:
            add("biotech_growth", BIOTECH_GROWTH_POINTS)
        else:
            add("biotech_niche", BIOTECH_NICHE_PENALTY)

    electronics = int(getattr(state.tech, "electronics", 0) or 0)
    if "electronics_floor" in tags and primary == "electronics" and electronics < 10:
        add("electronics_floor", ELECTRONICS_FLOOR_POINTS * min(1.0, (10 - electronics) / 4.0))

    total = sum(points for _name, points in rows)
    return total, tuple(rows)


def observed_enemy_weapon_level(state: Any) -> int | None:
    """Highest weapons tech actually seen on an enemy design, if any."""
    levels: list[int] = []
    native = getattr(state, "native", {}) or {}
    for key in ("enemy_designs", "enemy_design_profiles", "enemy_combat_profiles"):
        for row in native.get(key) or []:
            level = _weapon_level(row)
            if level is not None:
                levels.append(level)
    for fleet in getattr(state, "fleets", []) or []:
        owner = getattr(fleet, "owner", None)
        if owner in (None, getattr(state, "player_id", None)):
            continue
        raw = getattr(fleet, "native", {}) or {}
        for key in ("designs", "design_profiles", "slots"):
            payload = raw.get(key)
            if isinstance(payload, list):
                for row in payload:
                    level = _weapon_level(row)
                    if level is not None:
                        levels.append(level)
        level = _weapon_level(raw)
        if level is not None:
            levels.append(level)
    return max(levels) if levels else None


def _is_alternate_reality(state: Any) -> bool:
    from .planet_economy import decode_race_economy
    return bool(decode_race_economy(state.race).alternate_reality)


def _weapon_level(row: Any) -> int | None:
    if not isinstance(row, dict):
        return None
    if row.get("weapons") is not None:
        try:
            return int(row["weapons"])
        except (TypeError, ValueError):
            return None
    best = None
    for slot in row.get("slots") or []:
        if not isinstance(slot, dict):
            continue
        category = int(slot.get("category", 0) or 0)
        count = int(slot.get("count", 0) or 0)
        if count <= 0 or category not in (BEAM, TORPEDO):
            continue
        spec = component_spec(category, int(slot.get("item_id", 0) or 0))
        level = int(spec.tech_required[1])
        best = level if best is None else max(best, level)
    return best
