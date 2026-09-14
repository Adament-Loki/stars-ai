"""Conservative fleet organization and singleton-scout dispersion.

Stars! resolves combat by fleet/design tokens. A collection of independent
armed fleets at the same owned world therefore gives an enemy more targeting
and interception choices than one coordinated defensive force. This module
concentrates only fleets that are already idle, co-located, armed, and free of
all cargo or strategic tasks.

Scout fleets are the opposite case: stacking multiple scout hulls in one fleet
reduces parallel survey coverage. The planner therefore diagnoses excess scout
hulls as wasted survey capacity and requests singleton splits only for safe,
stationary, cargo-free, pure scout stacks.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .models import GameState, OrderSet


_COMBAT_ROLES = frozenset({"combat", "bomber", "escort", "warship"})
_CARGO_KEYS = ("ironium", "boranium", "germanium", "population")


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _ship_counts(fleet: Any) -> list[int]:
    """Return the native 16-slot ship-count vector, normalized to integers."""
    raw = (getattr(fleet, "native", {}) or {}).get("ship_count") or []
    try:
        values = list(raw)[:16]
    except TypeError:
        values = []
    counts: list[int] = []
    for value in values:
        try:
            counts.append(max(0, int(value or 0)))
        except (TypeError, ValueError):
            counts.append(0)
    counts.extend([0] * (16 - len(counts)))
    return counts


def _scout_slots(state: GameState) -> set[int]:
    """Return design slots whose current normalized design role is scout."""
    profiles = (state.native or {}).get("design_profiles") or {}
    slots: set[int] = set()
    if isinstance(profiles, dict):
        items = profiles.items()
    else:
        items = enumerate(profiles) if isinstance(profiles, list) else []
    for raw_slot, profile in items:
        if not isinstance(profile, dict):
            continue
        if str(profile.get("role", "") or "").casefold() != "scout":
            continue
        try:
            slot = int(raw_slot)
        except (TypeError, ValueError):
            try:
                slot = int(profile.get("design_slot"))
            except (TypeError, ValueError):
                continue
        if 0 <= slot < 16:
            slots.add(slot)
    return slots


def _is_armed(fleet: Any) -> bool:
    native = getattr(fleet, "native", {}) or {}
    return (
        str(getattr(fleet, "role", "") or "").casefold() in _COMBAT_ROLES
        or bool(native.get("armed") or native.get("has_weapons"))
        or _number(native.get("weapon_count")) > 0.0
        or _number(getattr(fleet, "combat_power", 0.0)) > 0.0
    )


def _combat_value(fleet: Any) -> float:
    """Comparable strength for selecting the retained fleet, never zero."""
    native = getattr(fleet, "native", {}) or {}
    for value in (
        getattr(fleet, "combat_power", 0.0),
        native.get("offensive_rating"),
        native.get("combat_readiness"),
        native.get("combat_power"),
        native.get("mass"),
        (native.get("fuel_profile") or {}).get("dry_mass"),
    ):
        if _number(value) > 0.0:
            return _number(value)
    return 1.0


def _has_cargo(fleet: Any) -> bool:
    native = getattr(fleet, "native", {}) or {}
    if _number(getattr(fleet, "cargo_population", 0)) > 0:
        return True
    cargo = native.get("cargo") or {}
    return any(_number(cargo.get(key)) > 0 for key in _CARGO_KEYS)


def _has_existing_order(orders: OrderSet, fleet_id: int) -> bool:
    for order in orders.orders:
        payload = order.payload or {}
        ids = [payload.get("fleet_id"), payload.get("target_fleet_id")]
        ids.extend(payload.get("source_fleet_ids") or [])
        for candidate in ids:
            try:
                if int(candidate) == int(fleet_id):
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _owned_anchor(state: GameState, fleet: Any):
    """Return the owned world occupied by a fleet, with a native-ID first rule."""
    native = getattr(fleet, "native", {}) or {}
    try:
        object_id = int(native.get("position_object_id", -1) or -1)
    except (TypeError, ValueError):
        object_id = -1
    for planet in state.planets:
        if planet.owner == state.player_id and int(planet.id) == object_id:
            return planet
    for planet in state.planets:
        if (
            planet.owner == state.player_id
            and abs(float(fleet.position.x) - float(planet.position.x)) <= 0.5
            and abs(float(fleet.position.y) - float(planet.position.y)) <= 0.5
        ):
            return planet
    return None


def _is_stationary_for_scout_split(fleet: Any) -> bool:
    native = getattr(fleet, "native", {}) or {}
    if getattr(fleet, "destination_planet_id", None) is not None:
        return False
    if native.get("native_destination_fleet_id") is not None:
        return False
    if getattr(fleet, "destination_task", None) is not None:
        return False
    waypoints = native.get("waypoints")
    if isinstance(waypoints, (list, tuple)) and len(waypoints) > 1:
        return False
    try:
        if int(native.get("native_waypoint_count", 1) or 1) > 1:
            return False
    except (TypeError, ValueError):
        pass
    return True


def _plan_scout_dispersion(state: GameState, orders: OrderSet) -> list[dict[str, Any]]:
    """Diagnose stacked scouts and request safe singleton splits.

    Wasted survey capacity is the number of extra scout hulls trapped in a
    stacked fleet: max(0, ship_count[scout_slot] - 1). Diagnostics cover every
    owned fleet; automatic native split requests are intentionally narrower.
    """
    scout_slots = _scout_slots(state)
    stacked: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    wasted_total = 0

    for fleet in state.fleets:
        if int(getattr(fleet, "owner", -1)) != int(state.player_id):
            continue
        counts = _ship_counts(fleet)
        scout_total = sum(counts[slot] for slot in scout_slots)
        non_scout_total = sum(count for slot, count in enumerate(counts) if slot not in scout_slots)
        for slot in sorted(scout_slots):
            count = counts[slot]
            if count <= 1:
                continue
            wasted = count - 1
            wasted_total += wasted
            diagnostic = {
                "fleet_id": int(fleet.id),
                "scout_slot": slot,
                "ship_count": count,
                "wasted_survey_capacity": wasted,
                "pure_scout_stack": non_scout_total == 0 and scout_total == count,
            }
            stacked.append(diagnostic)

            safe_to_split = (
                diagnostic["pure_scout_stack"]
                and not _has_cargo(fleet)
                and not _has_existing_order(orders, int(fleet.id))
                and _is_stationary_for_scout_split(fleet)
                and 0 <= int(fleet.id) <= 0x1FF
            )
            if not safe_to_split:
                continue

            payload = {
                "fleet_id": int(fleet.id),
                "scout_slot": slot,
                "ship_count": count,
                "singleton_fleets_required": wasted,
                "mission": "split_scouts_for_parallel_survey",
                "scout_stack_diagnostic": diagnostic,
                "native_experiment": {
                    "enabled": True,
                    "trust_level": "NEEDS_HOST_VALIDATION",
                    "id": "type24-type23-singleton-scout-split",
                    "risk": (
                        "Synthesizes Type24 FleetSplit plus Type23 MoveShips for one scout at a time; "
                        "restricted to stationary cargo-free pure scout stacks."
                    ),
                },
            }
            orders.add(
                "split_scout_fleet",
                payload,
                (
                    f"Split stacked scout fleet {fleet.id}: design slot {slot} has {count} scouts, "
                    f"wasting {wasted} parallel survey slot(s). Create {wasted} singleton scout fleet(s)."
                ),
                priority=140,
            )
            decisions.append(payload)

    state.native["scout_stack_diagnostics"] = {
        "scout_slots": sorted(scout_slots),
        "stacked_fleet_count": len({item["fleet_id"] for item in stacked}),
        "wasted_survey_capacity": wasted_total,
        "stacked_fleets": stacked,
        "split_orders": len(decisions),
    }
    if stacked:
        orders.notes.append(
            f"SCOUT STACK DIAGNOSTIC: {wasted_total} wasted survey capacity across "
            f"{len({item['fleet_id'] for item in stacked})} stacked scout fleet(s); "
            f"queued {len(decisions)} safe singleton-split order(s)."
        )

    # TODO: add regression tests for 1/2/3+ scout stacks and validate the
    # emitted Type-24 -> Type-23 singleton transfer sequence against a fresh
    # Stars! client capture/host replay before promoting native scout splitting
    # to VALIDATED.
    return decisions


def _is_idle_local_combat_fleet(state: GameState, orders: OrderSet, fleet: Any) -> bool:
    native = getattr(fleet, "native", {}) or {}
    if int(getattr(fleet, "owner", -1)) != int(state.player_id):
        return False
    if str(getattr(fleet, "role", "") or "").casefold() not in _COMBAT_ROLES:
        return False
    if not _is_armed(fleet) or _has_cargo(fleet):
        return False
    if str(getattr(fleet, "role", "") or "").casefold() == "minelayer":
        return False
    if _has_existing_order(orders, int(fleet.id)):
        return False
    if getattr(fleet, "destination_planet_id", None) is not None:
        return False
    if native.get("native_destination_fleet_id") is not None:
        return False
    if getattr(fleet, "destination_task", None) is not None:
        return False
    if not 0 <= int(fleet.id) <= 0x1FF:
        return False
    return _owned_anchor(state, fleet) is not None


def _battle_policy(state: GameState) -> dict[str, bool]:
    """Normalize host-rule-sensitive tactics; defaults always favour fairness."""
    supplied = (state.native or {}).get("battle_policy") or {}
    return {
        "allow_chaff": bool(supplied.get("allow_chaff", False)),
        "allow_split_dodge": bool(supplied.get("allow_split_dodge", False)),
        "allow_minimum_damage_split": False,
        "allow_scrap_reclamation_exploit": False,
    }


def add_tactical_fleet_management(
    state: GameState,
    orders: OrderSet,
    plan: Any | None = None,
) -> list[dict[str, Any]]:
    """Split safe scout stacks, then merge safe co-located armed fleets."""
    scout_splits = _plan_scout_dispersion(state, orders)
    policy = _battle_policy(state)
    groups: dict[int, list[Any]] = defaultdict(list)
    anchors: dict[int, Any] = {}
    for fleet in state.fleets:
        if not _is_idle_local_combat_fleet(state, orders, fleet):
            continue
        anchor = _owned_anchor(state, fleet)
        if anchor is None:
            continue
        groups[int(anchor.id)].append(fleet)
        anchors[int(anchor.id)] = anchor

    decisions: list[dict[str, Any]] = []
    for planet_id, fleets in sorted(groups.items()):
        if len(fleets) < 2:
            continue
        retained = max(fleets, key=lambda fleet: (_combat_value(fleet), -int(fleet.id)))
        sources = sorted(int(fleet.id) for fleet in fleets if int(fleet.id) != int(retained.id))
        if not sources:
            continue
        anchor = anchors[planet_id]
        total_value = sum(_combat_value(fleet) for fleet in fleets)
        payload = {
            "fleet_id": int(retained.id),
            "target_fleet_id": int(retained.id),
            "source_fleet_ids": sources,
            "destination_planet_id": int(anchor.id),
            "mission": "concentrate_for_battle",
            "battle_management": {
                "policy": "concentrate_idle_armed_fleets",
                "estimated_combined_offensive_value": round(total_value, 2),
                "planet_id": int(anchor.id),
            },
            "native_reference": "Captured Type37 merge operation; combat concentration uses only same-location idle fleets.",
            "native_experiment": {
                "enabled": True,
                "trust_level": "CLIENT_CAPTURED_LAYOUT",
                "id": "type37-fleet-concentration",
                "risk": "Only co-located idle armed fleets are merged; no routes, cargo, or minefield assignments are changed.",
            },
        }
        orders.add(
            "merge_fleets",
            payload,
            (
                f"Concentrate {len(fleets)} idle armed fleets at {anchor.name} into "
                f"fleet {retained.id}; combined estimated offensive value={total_value:.1f}. "
                "This creates one coordinated defensive force instead of several independent targets."
            ),
            priority=118,
        )
        decisions.append({
            "planet_id": int(anchor.id),
            "planet_name": anchor.name,
            "retained_fleet_id": int(retained.id),
            "source_fleet_ids": sources,
            "fleet_count": len(fleets),
            "estimated_combined_offensive_value": round(total_value, 2),
        })

    state.native["fleet_battle_management"] = {
        "mode": "concentrate_idle_armed_fleets",
        "type37_merge_capability": "PARTIAL",
        "scout_singleton_splits": len(scout_splits),
        "merges": decisions,
        "battle_policy": policy,
        "safeguards": [
            "No automatic chaff program unless a game-specific host rule explicitly enables it.",
            "Scout splitting is limited to stationary cargo-free pure scout stacks for survey parallelism.",
            "No combat split-fleet dodge tactics are generated.",
            "Minimum-damage fleet splitting is prohibited.",
            "UR/CE allied scrap-reclamation exploitation is prohibited.",
        ],
    }
    if decisions:
        orders.notes.append(
            f"BATTLE FLEET MANAGEMENT: ordered {len(decisions)} local concentration merge(s); "
            "no combat split or exploit tactics were used."
        )
    return decisions
