"""Conservative battle-fleet concentration using the validated Type-37 merge.

Stars! resolves combat by fleet/design tokens.  A collection of independent
armed fleets at the same owned world therefore gives an enemy more targeting
and interception choices than one coordinated defensive force.  This module
concentrates only fleets that are already idle, co-located, armed, and free of
all cargo or strategic tasks.  It deliberately does not create fleet splits:
the native adapter has no validated split operation, and automatic split-based
minimum-damage or dodge tactics are inappropriate without explicit host rules.
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


def _is_idle_local_combat_fleet(state: GameState, orders: OrderSet, fleet: Any) -> bool:
    native = getattr(fleet, "native", {}) or {}
    if int(getattr(fleet, "owner", -1)) != int(state.player_id):
        return False
    # Scouts can be weapon-bearing, but the exploration pass runs later in the
    # turn and may legitimately give them a route.  Concentration is for
    # explicit combat formations only; this avoids creating a merge/movement
    # conflict that the native writer would have to discard.
    if str(getattr(fleet, "role", "") or "").casefold() not in _COMBAT_ROLES:
        return False
    if not _is_armed(fleet) or _has_cargo(fleet):
        return False
    # A minelayer's local hold is an active strategic job rather than a fleet
    # waiting to be organized for combat.
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
        # These are policy prohibitions, not merely unavailable features.
        "allow_minimum_damage_split": False,
        "allow_scrap_reclamation_exploit": False,
    }


def add_tactical_fleet_management(
    state: GameState,
    orders: OrderSet,
    plan: Any | None = None,
) -> list[dict[str, Any]]:
    """Merge safe co-located armed fleets and record battle-rule safeguards.

    The Type-37 encoding can merge multiple source fleets.  One retained fleet
    per world is selected by offensive/combat value, then by ID for stable
    replay.  The priority intentionally sits below immediate military movement
    so a threat response can reserve the same fleet without a conflict.
    """
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
        # Retain the best-ready fleet.  Choosing an existing ID instead of
        # synthesizing a new formation is necessary for the captured native
        # operation and preserves deterministic playtests.
        retained = max(
            fleets,
            key=lambda fleet: (_combat_value(fleet), -int(fleet.id)),
        )
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
                "This creates one coordinated defensive force instead of several "
                "independent targets."
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
        "merges": decisions,
        "battle_policy": policy,
        "safeguards": [
            "No automatic chaff program unless a game-specific host rule explicitly enables it.",
            "No automatic split-fleet dodge: native fleet splitting is not validated.",
            "Minimum-damage fleet splitting is prohibited.",
            "UR/CE allied scrap-reclamation exploitation is prohibited.",
        ],
    }
    if decisions:
        orders.notes.append(
            f"BATTLE FLEET MANAGEMENT: ordered {len(decisions)} local concentration merge(s); "
            "no split or exploit tactics were used."
        )
    return decisions
