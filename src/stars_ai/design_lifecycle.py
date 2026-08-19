
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any

SHIP_DESIGN_LIMIT = 16
STARBASE_DESIGN_LIMIT = 10

class DesignDisposition(str, Enum):
    KEEP_FIRST_LINE = "keep_first_line"
    KEEP_SECOND_LINE = "keep_second_line"
    KEEP_SPECIALIZED = "keep_specialized"
    EXPEND = "expend"
    RECYCLE = "recycle"
    DELETE_WHEN_EMPTY = "delete_when_empty"
    PROTECT_SLOT = "protect_slot"

@dataclass
class DesignLifecycleAssessment:
    design_slot: int
    label: str
    is_starbase: bool
    active_count: int
    combat_efficiency: float
    obsolete_score: float
    secondary_role_value: float
    uniqueness_value: float
    replacement_value: float
    slot_pressure: float
    disposition: DesignDisposition
    priority: float
    reason: str

@dataclass
class DesignSlotPressure:
    used_ship_slots: int
    free_ship_slots: int
    used_starbase_slots: int
    free_starbase_slots: int
    ship_pressure: float
    starbase_pressure: float

def _slot_number(d: Any) -> int:
    for name in ("design_number", "slot", "design_slot"):
        value = getattr(d, name, None)
        if value is not None:
            return int(value)
    return -1

def slot_pressure(
    designs: list[Any],
    *,
    ship_limit: int = SHIP_DESIGN_LIMIT,
    starbase_limit: int = STARBASE_DESIGN_LIMIT,
) -> DesignSlotPressure:
    seen_ship = set()
    seen_base = set()
    for d in designs:
        slot = _slot_number(d)
        is_base = bool(getattr(d, "is_starbase", getattr(d, "starbase", False)))
        if slot < 0:
            continue
        (seen_base if is_base else seen_ship).add(slot)

    ship_used = len(seen_ship)
    base_used = len(seen_base)
    ship_free = max(0, ship_limit - ship_used)
    base_free = max(0, starbase_limit - base_used)

    def pressure(used: int, limit: int) -> float:
        if limit <= 0:
            return 1.0
        ratio = used / limit
        if ratio < 0.70:
            return ratio * 0.45
        if ratio < 0.90:
            return 0.315 + (ratio - 0.70) / 0.20 * 0.35
        return min(1.0, 0.665 + (ratio - 0.90) / 0.10 * 0.335)

    return DesignSlotPressure(
        used_ship_slots=ship_used,
        free_ship_slots=ship_free,
        used_starbase_slots=base_used,
        free_starbase_slots=base_free,
        ship_pressure=pressure(ship_used, ship_limit),
        starbase_pressure=pressure(base_used, starbase_limit),
    )

def assess_design_lifecycle(
    *,
    design_slot: int,
    label: str,
    is_starbase: bool,
    active_count: int,
    combat_efficiency: float,
    obsolete_score: float,
    secondary_role_value: float,
    uniqueness_value: float,
    replacement_value: float,
    slot_pressure_value: float,
) -> DesignLifecycleAssessment:
    active_count = max(0, int(active_count))
    obsolete_score = max(0.0, min(1.0, obsolete_score))
    secondary_role_value = max(0.0, min(1.0, secondary_role_value))
    uniqueness_value = max(0.0, min(1.0, uniqueness_value))
    replacement_value = max(0.0, min(1.0, replacement_value))
    slot_pressure_value = max(0.0, min(1.0, slot_pressure_value))

    first_line_value = max(0.0, combat_efficiency) * (1.0 - obsolete_score)
    retain_value = (
        0.42 * min(1.0, first_line_value)
        + 0.30 * secondary_role_value
        + 0.28 * uniqueness_value
    )
    recycle_pressure = (
        0.45 * replacement_value
        + 0.35 * slot_pressure_value
        + 0.20 * obsolete_score
    )
    if is_starbase:
        recycle_pressure *= 0.88

    if active_count == 0:
        if recycle_pressure > retain_value + 0.08:
            disposition = DesignDisposition.RECYCLE
            reason = "No surviving units use this design and the slot is more valuable for a replacement."
        else:
            disposition = DesignDisposition.PROTECT_SLOT
            reason = "The empty-in-use slot still has enough unique or future value to retain temporarily."
    elif obsolete_score < 0.30 and first_line_value >= 0.45:
        disposition = DesignDisposition.KEEP_FIRST_LINE
        reason = "Design remains effective enough for current peer combat."
    elif uniqueness_value >= 0.70:
        disposition = DesignDisposition.KEEP_SPECIALIZED
        reason = "Design is aging but provides a scarce capability that is not safely replaceable yet."
    elif secondary_role_value >= 0.58 and recycle_pressure < 0.78:
        disposition = DesignDisposition.KEEP_SECOND_LINE
        reason = "Design is obsolete for first-line combat but remains useful for secondary missions."
    elif recycle_pressure >= 0.72 and secondary_role_value >= 0.30:
        disposition = DesignDisposition.EXPEND
        reason = "The slot is needed soon; use remaining ships in useful low-value/screening roles rather than preserve them indefinitely."
    elif recycle_pressure >= 0.62:
        disposition = DesignDisposition.DELETE_WHEN_EMPTY
        reason = "Stop producing this design and free the slot once surviving units are gone."
    else:
        disposition = DesignDisposition.KEEP_SECOND_LINE
        reason = "The design is not first-line, but current slot pressure does not justify forced retirement."

    priority = max(0.0, min(1.0, recycle_pressure - 0.45 * retain_value + 0.25))
    return DesignLifecycleAssessment(
        design_slot=design_slot,
        label=label,
        is_starbase=is_starbase,
        active_count=active_count,
        combat_efficiency=combat_efficiency,
        obsolete_score=obsolete_score,
        secondary_role_value=secondary_role_value,
        uniqueness_value=uniqueness_value,
        replacement_value=replacement_value,
        slot_pressure=slot_pressure_value,
        disposition=disposition,
        priority=priority,
        reason=reason,
    )

def secondary_role_value(
    *,
    can_screen: bool=False,
    useful_at_starbase: bool=False,
    can_overmatch_unarmed: bool=False,
    can_overmatch_older: bool=False,
    useful_as_escort: bool=False,
    useful_as_raider: bool=False,
) -> float:
    score = 0.0
    score += 0.20 if can_screen else 0.0
    score += 0.22 if useful_at_starbase else 0.0
    score += 0.18 if can_overmatch_unarmed else 0.0
    score += 0.15 if can_overmatch_older else 0.0
    score += 0.12 if useful_as_escort else 0.0
    score += 0.13 if useful_as_raider else 0.0
    return min(1.0, score)

def lifecycle_actions(a: DesignLifecycleAssessment) -> list[str]:
    if a.disposition == DesignDisposition.KEEP_FIRST_LINE:
        return ["Continue production if strategically needed.", "Use in peer combat based on local force ratio."]
    if a.disposition == DesignDisposition.KEEP_SECOND_LINE:
        return ["Stop/reduce new production.", "Use for starbase defense, screening, escorts, raiding, or weaker targets.", "Reassess as slot pressure rises."]
    if a.disposition == DesignDisposition.KEEP_SPECIALIZED:
        return ["Retain until replacement capability exists.", "Do not sacrifice solely to free a slot."]
    if a.disposition == DesignDisposition.EXPEND:
        return ["Do not build more.", "Use remaining hulls where losses still buy useful time or enemy attention.", "Prefer screens, starbase support, anti-unarmed duty, raids, or secondary fronts.", "Recycle the slot once the design has no surviving hulls."]
    if a.disposition == DesignDisposition.DELETE_WHEN_EMPTY:
        return ["Stop production immediately.", "Do not invest major resources preserving these hulls.", "Recycle the slot once no surviving hulls remain."]
    if a.disposition == DesignDisposition.RECYCLE:
        return ["Free the design slot for the higher-value replacement."]
    return ["Retain until a clearer replacement need emerges."]
