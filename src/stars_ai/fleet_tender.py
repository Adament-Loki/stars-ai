"""Inner Strength Fuel Xport doctrine and annual repair accounting.

Fuel Transport and Super-Fuel Xport hulls are fleet tenders.  Their repair
effect is resolved by Stars! between turns, while stopped or orbiting; it is
not a battle-board ability and must never be treated as combat regeneration.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .util import distance


FUEL_TRANSPORT_HULL_ID = 25
SUPER_FUEL_XPORT_HULL_ID = 26
ANNUAL_TENDER_FUEL_MG = 200


@dataclass(frozen=True)
class TenderCapability:
    hull_id: int
    hull_name: str
    annual_repair_bonus_percentage_points: int
    annual_fuel_mg: int = ANNUAL_TENDER_FUEL_MG


FLEET_TENDER_CAPABILITIES = {
    FUEL_TRANSPORT_HULL_ID: TenderCapability(
        FUEL_TRANSPORT_HULL_ID, "Fuel Transport", 10,
    ),
    SUPER_FUEL_XPORT_HULL_ID: TenderCapability(
        SUPER_FUEL_XPORT_HULL_ID, "Super-Fuel Xport", 15,
    ),
}


def is_inner_strength(state: Any) -> bool:
    """Whether the current race is the only race allowed to use Fuel Xports."""
    native = getattr(getattr(state, "race", None), "native", {}) or {}
    return int(native.get("prt_id", -1) or -1) == 4


def annual_starbase_repair_percentage_points(state: Any) -> int:
    """Stars! annual base repair: 10% normally, 15% for Inner Strength bases.

    This deliberately lives apart from fleet repair.  The Inner Strength
    benefit applies to starbases, not to ordinary fleet repair.
    """
    return 15 if is_inner_strength(state) else 10


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _records_by_design_slot(state: Any) -> dict[int, dict[str, Any]]:
    """Return native design/profile records indexed by the local design slot."""
    records: dict[int, dict[str, Any]] = {}
    native = getattr(state, "native", {}) or {}
    for key in ("design_profiles", "designs"):
        values = native.get(key, []) or []
        iterable = values.values() if isinstance(values, dict) else values
        for raw in iterable:
            if not isinstance(raw, dict):
                continue
            slot = _as_int(raw.get("design_number", raw.get("slot")))
            if slot is not None and 0 <= slot < 16:
                records[slot] = {**records.get(slot, {}), **raw}
    return records


def fleet_tender_hull_ids(fleet: Any, state: Any) -> set[int]:
    """Return known Fuel Xport hull IDs currently in ``fleet``.

    The normal path joins decoded ship counts with the native design profile.
    The small direct fields make the helper usable in narrow replay fixtures
    without allowing a name such as "Tank Tender" to masquerade as a Fuel
    Transport.
    """
    native = getattr(fleet, "native", {}) or {}
    found: set[int] = set()
    direct = native.get("hull_id")
    if _as_int(direct) in FLEET_TENDER_CAPABILITIES:
        found.add(int(direct))
    for raw in native.get("hull_ids", []) or []:
        hull_id = _as_int(raw)
        if hull_id in FLEET_TENDER_CAPABILITIES:
            found.add(int(hull_id))

    counts = native.get("ship_count", getattr(fleet, "ship_count", [])) or []
    if not isinstance(counts, (list, tuple)):
        return found
    designs = _records_by_design_slot(state)
    for design_slot, count in enumerate(counts[:16]):
        if int(count or 0) <= 0:
            continue
        hull_id = _as_int((designs.get(design_slot) or {}).get("hull_id"))
        if hull_id in FLEET_TENDER_CAPABILITIES:
            found.add(int(hull_id))
    return found


def fleet_tender_capability(fleet: Any, state: Any) -> TenderCapability | None:
    """Return one tender benefit; Fuel Xport repair bonuses do not stack."""
    present = [FLEET_TENDER_CAPABILITIES[hull_id] for hull_id in fleet_tender_hull_ids(fleet, state)]
    return max(
        present,
        key=lambda capability: capability.annual_repair_bonus_percentage_points,
        default=None,
    )


def decoded_damage_evidence(fleet: Any) -> dict[str, Any] | None:
    """Expose confirmed fleet damage without inventing a percentage conversion.

    Full fleet records carry one opaque 16-bit damage entry for every damaged
    design type.  StarsAPI preserves that entry but does not define a safe
    public percentage conversion, so the planner uses nonzero entries solely
    to decide whether there is damage worth repairing.
    """
    native = getattr(fleet, "native", {}) or {}
    raw = native.get("damage")
    entries: list[tuple[int, int]] = []
    if isinstance(raw, dict):
        for slot, value in raw.items():
            slot_i, value_i = _as_int(slot), _as_int(value)
            if slot_i is not None and value_i is not None and value_i > 0:
                entries.append((slot_i, value_i))
    elif isinstance(raw, (list, tuple)):
        for slot, value in enumerate(raw[:16]):
            value_i = _as_int(value)
            if value_i is not None and value_i > 0:
                entries.append((slot, value_i))
    if entries:
        return {
            "source": "decoded_damage_by_design",
            "damaged_design_slots": [slot for slot, _value in entries],
            "raw_damage_entries": {str(slot): value for slot, value in entries},
        }

    # Some non-native integration callers supply an explicit UI percentage.
    # It is useful evidence, but it is never combined with the opaque native
    # entries or mistaken for a host-side repair result.
    for key in ("damage_percent", "damage_percentage"):
        value = _as_int(native.get(key))
        if value is not None and value > 0:
            return {"source": key, "damage_percent": value}
    return None


def fleet_has_confirmed_damage(fleet: Any) -> bool:
    return decoded_damage_evidence(fleet) is not None


def _fleet_fuel_values(fleet: Any) -> tuple[int, int] | None:
    """Return decoded (current fuel, capacity), or None if either is unknown."""
    native = getattr(fleet, "native", {}) or {}
    profile = native.get("fuel_profile") or {}
    current = _as_int(profile.get("fuel", native.get("fuel")))
    capacity = _as_int(profile.get("fuel_capacity"))
    if current is None or capacity is None or capacity < 0:
        return None
    return max(0, current), max(0, capacity)


def projected_tender_fuel_gain(fleets: list[Any]) -> dict[str, Any]:
    """Project one tender's annual fuel, capped by the merged fleet capacity.

    The repair rule is explicitly non-stacking.  The doctrine therefore keeps
    one qualifying tender with a task force and credits only one 200mg annual
    production event; capacity that cannot store fuel has zero value.
    """
    values = [_fleet_fuel_values(fleet) for fleet in fleets]
    if any(value is None for value in values):
        return {
            "annual_fuel_mg": ANNUAL_TENDER_FUEL_MG,
            "projected_generated_fuel_mg": None,
            "fuel_capacity_status": "unknown",
        }
    current = sum(value[0] for value in values if value is not None)
    capacity = sum(value[1] for value in values if value is not None)
    spare = max(0, capacity - current)
    return {
        "annual_fuel_mg": ANNUAL_TENDER_FUEL_MG,
        "current_fuel_mg": current,
        "fuel_capacity_mg": capacity,
        "available_storage_mg": spare,
        "projected_generated_fuel_mg": min(ANNUAL_TENDER_FUEL_MG, spare),
        "fuel_capacity_status": "decoded",
    }


def fleet_tender_annual_forecast(fleet: Any, state: Any) -> dict[str, Any] | None:
    """Audit a tender benefit for a fleet that already contains the tender.

    This is a forecast of the *next annual repair step*, never an instruction
    to add health this turn.  A stationary-orbiting check is intentionally
    conservative because moving fleets use a different, lower base rate.
    """
    capability = fleet_tender_capability(fleet, state)
    if capability is None:
        return None
    native = getattr(fleet, "native", {}) or {}
    stationary = (
        getattr(fleet, "destination_planet_id", None) is None
        and native.get("native_destination_fleet_id") is None
    )
    return {
        "tender": asdict(capability),
        "repair_timing": "annual_inter_turn",
        "combat_regeneration": False,
        "same_fleet_required": True,
        "repair_bonus_stacks": False,
        "eligible_for_stopped_or_orbiting_bonus": stationary,
        "damage_evidence": decoded_damage_evidence(fleet),
        "fuel": projected_tender_fuel_gain([fleet]),
    }


def _has_order_for_fleet(orders: Any, fleet_id: int) -> bool:
    for order in getattr(orders, "orders", []) or []:
        payload = getattr(order, "payload", {}) or {}
        identifiers = [payload.get("fleet_id"), payload.get("target_fleet_id")]
        identifiers.extend(payload.get("source_fleet_ids") or [])
        for raw in identifiers:
            value = _as_int(raw)
            if value is not None and value == int(fleet_id):
                return True
    return False


def _co_located(left: Any, right: Any) -> bool:
    return (
        abs(float(left.position.x) - float(right.position.x)) <= 0.5
        and abs(float(left.position.y) - float(right.position.y)) <= 0.5
    )


def _has_immediate_interception_risk(state: Any, fleet: Any) -> bool:
    """Avoid delaying a threatened formation merely to seek repair support."""
    native = getattr(state, "native", {}) or {}
    explicit = {
        int(value) for value in (native.get("immediate_threat_fleet_ids", []) or [])
        if _as_int(value) is not None
    }
    if int(fleet.id) in explicit:
        return True
    return any(
        other.owner not in (None, state.player_id)
        and distance(fleet.position, other.position) <= 0.5
        for other in getattr(state, "fleets", []) or []
    )


def _idle_repair_candidate(state: Any, orders: Any, fleet: Any) -> bool:
    native = getattr(fleet, "native", {}) or {}
    return (
        fleet.owner == state.player_id
        and str(getattr(fleet, "role", "") or "").casefold() == "combat"
        and fleet_has_confirmed_damage(fleet)
        and fleet_tender_capability(fleet, state) is None
        and not _has_order_for_fleet(orders, int(fleet.id))
        and getattr(fleet, "destination_planet_id", None) is None
        and native.get("native_destination_fleet_id") is None
        and not _has_immediate_interception_risk(state, fleet)
        and 0 <= int(fleet.id) <= 0x1FF
    )


def add_fleet_tender_repair_orders(state: Any, orders: Any) -> list[dict[str, Any]]:
    """Merge a damaged idle combat fleet with one co-located real tender.

    Type-37 can prove the merge, but cannot safely combine an unvalidated
    rendezvous, merge, and retarget transaction.  This planner therefore only
    acts after a rendezvous has already completed and retains the combat fleet
    as the target.  The next M-file read-back is the earliest moment at which
    the annual bonus can be expected.
    """
    decisions: list[dict[str, Any]] = []
    candidates = [
        fleet for fleet in getattr(state, "fleets", []) or []
        if _idle_repair_candidate(state, orders, fleet)
    ]
    tenders = [
        fleet for fleet in getattr(state, "fleets", []) or []
        if fleet.owner == state.player_id
        and fleet_tender_capability(fleet, state) is not None
        and not _has_order_for_fleet(orders, int(fleet.id))
        and getattr(fleet, "destination_planet_id", None) is None
        and (getattr(fleet, "native", {}) or {}).get("native_destination_fleet_id") is None
        and 0 <= int(fleet.id) <= 0x1FF
    ]
    used_tenders: set[int] = set()
    for combat_fleet in sorted(candidates, key=lambda fleet: int(fleet.id)):
        usable = [
            tender for tender in tenders
            if int(tender.id) not in used_tenders and _co_located(tender, combat_fleet)
        ]
        if not usable:
            continue
        tender = max(
            usable,
            key=lambda candidate: (
                int(fleet_tender_capability(candidate, state).annual_repair_bonus_percentage_points),
                int(fleet_tender_capability(candidate, state).hull_id),
                -int(candidate.id),
            ),
        )
        capability = fleet_tender_capability(tender, state)
        if capability is None:
            continue
        damage = decoded_damage_evidence(combat_fleet)
        fuel = projected_tender_fuel_gain([combat_fleet, tender])
        payload = {
            "fleet_id": int(combat_fleet.id),
            "target_fleet_id": int(combat_fleet.id),
            "source_fleet_ids": [int(tender.id)],
            "mission": "fleet_tender_repair_merge",
            "fleet_tender": {
                "hull_id": capability.hull_id,
                "hull_name": capability.hull_name,
                "annual_repair_bonus_percentage_points": capability.annual_repair_bonus_percentage_points,
                "annual_fuel_mg": capability.annual_fuel_mg,
                "repair_timing": "annual_inter_turn",
                "combat_regeneration": False,
                "same_fleet_required": True,
                "repair_bonus_stacks": False,
                "damage_evidence": damage,
                "projected_fuel": fuel,
            },
            "native_reference": "Captured Type37 merge: merge an already co-located tender into the damaged combat fleet before annual repair.",
            "native_experiment": {
                "enabled": True,
                "trust_level": "CLIENT_CAPTURED_LAYOUT",
                "id": "type37-fleet-tender-repair",
                "risk": "No rendezvous or retarget is synthesized; annual repair is host-resolved after next M-file read-back.",
            },
        }
        orders.add(
            "merge_fleets",
            payload,
            (
                f"Merge co-located {capability.hull_name} {tender.name} into damaged combat fleet "
                f"{combat_fleet.name}. It adds {capability.annual_repair_bonus_percentage_points} annual "
                "repair percentage points after the host resolves the inter-turn repair step; it supplies no combat healing."
            ),
            priority=132,
        )
        used_tenders.add(int(tender.id))
        decisions.append({
            "combat_fleet_id": int(combat_fleet.id),
            "tender_fleet_id": int(tender.id),
            "tender": asdict(capability),
            "damage_evidence": damage,
            "projected_fuel": fuel,
        })

    native = getattr(state, "native", None)
    if isinstance(native, dict):
        native["fleet_tender_doctrine"] = {
            "repair_timing": "annual_inter_turn",
            "combat_regeneration": False,
            "same_fleet_required": True,
            "repair_bonus_stacks": False,
            "annual_fuel_mg_per_qualifying_tender": ANNUAL_TENDER_FUEL_MG,
            "starbase_annual_repair_percentage_points": annual_starbase_repair_percentage_points(state),
            "merges": decisions,
        }
    return decisions
