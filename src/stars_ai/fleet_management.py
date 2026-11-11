"""Split stacked hulls onto their own fleets, and join fleets that share one job.

A stack can fly only one route. Scouts, freighters, colony ships, miners, and
any other non-battle hull are split down to one ship per fleet while they are
stationary and empty. Battle hulls, including fighters, stay stacked or are
joined when an enemy is already at that world. They are split when the world
is quiet, so each ship can be sent somewhere else on the following turn.
Loaded fleets and fleets already on a route are left alone. Joining for a
haul that does not fit in one hold stays in shared transport.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .models import GameState, OrderSet


_COMBAT_ROLES = frozenset({"combat", "bomber", "escort", "warship", "fighter"})
_BATTLE_ROLES = frozenset({"combat", "bomber", "escort", "warship", "fighter"})
_CARGO_KEYS = ("ironium", "boranium", "germanium", "population")
_SPLIT_ORDER_KINDS = frozenset({"split_scout_fleet", "split_fleet"})
_STACK_ROUTINE_ORDER_KINDS = frozenset({
    "move_fleet", "cancel_scout_route", "remote_mine", "lay_minefield",
    "intercept_fleet", "colony_operation", "transport_population",
    "transport_minerals", "transport_unload_remainder",
})


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


def _design_roles(state: GameState) -> dict[int, str]:
    """Return design slot to role, using the decoded design number as the slot."""
    profiles = (state.native or {}).get("design_profiles") or {}
    roles: dict[int, str] = {}
    if isinstance(profiles, dict):
        items = profiles.items()
    else:
        items = enumerate(profiles) if isinstance(profiles, list) else []
    for raw_slot, profile in items:
        if not isinstance(profile, dict):
            continue
        # List-shaped profile tables are often sparse: their list position is
        # not necessarily the native design slot.
        slot_value = profile.get("design_number")
        if slot_value is None:
            slot_value = profile.get("design_slot", raw_slot)
        try:
            slot = int(slot_value)
        except (TypeError, ValueError):
            try:
                slot = int(raw_slot)
            except (TypeError, ValueError):
                continue
        if 0 <= slot < 16:
            roles[slot] = str(profile.get("role", "") or "").casefold()
    return roles


def _scout_slots(state: GameState) -> set[int]:
    """Return design slots whose current normalized design role is scout."""
    return {slot for slot, role in _design_roles(state).items() if role == "scout"}


def _slot_organization(role: str, fleet: Any) -> str:
    """How a stacked design should be organized: parallel, battle, or leave."""
    fleet_role = str(getattr(fleet, "role", "") or "").casefold()
    effective = str(role or "").casefold() or fleet_role
    if effective == "minelayer":
        return "leave"
    if effective in _BATTLE_ROLES:
        return "battle"
    return "parallel"


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


def _is_emergency_fleet_recovery_order(order: Any, fleet_id: int) -> bool:
    """Whether an order is a genuine recovery exception to scout splitting.

    A normal scan/recon movement is never a reason to leave five scouts in one
    fleet.  Actual fuel/range recovery is different: breaking that fleet apart
    before it reaches safety can make every singleton unrecoverable.  Keep the
    exception deliberately narrow and evidence-driven.
    """
    payload = getattr(order, "payload", {}) or {}
    try:
        applies = int(payload.get("fleet_id", -1)) == int(fleet_id)
    except (TypeError, ValueError):
        applies = False
    if not applies:
        return False
    mission = str(payload.get("mission", "") or "").casefold()
    return bool(
        payload.get("emergency")
        or payload.get("fleet_recovery")
        or payload.get("colony_range_recovery")
        or "recovery" in mission
        or "rescue" in mission
    )


def enforce_scout_split_precedence(state: GameState, orders: OrderSet) -> list[dict[str, Any]]:
    """Make a safe singleton split win over routine orders for its source fleet.

    Type-24/23 creates fleet identities the current M file does not contain.
    Therefore this turn emits only the split; ordinary routes are deliberately
    reconsidered after host read-back, when every singleton can receive its
    own target.  A narrow fuel/range recovery exception retains the recovery
    and drops the split request instead.
    """
    # This is deliberately a hard barrier, rather than an ordering convention
    # between planners.  A caller which only proposed an ordinary movement
    # still gets a split request before that movement is considered.
    _plan_scout_dispersion(state, orders)

    split_by_fleet: dict[int, Any] = {}
    for order in orders.orders:
        if order.kind not in _SPLIT_ORDER_KINDS:
            continue
        try:
            split_by_fleet[int((order.payload or {}).get("fleet_id", -1))] = order
        except (TypeError, ValueError):
            continue
    if not split_by_fleet:
        return []

    decisions: list[dict[str, Any]] = []
    removed: set[int] = set()
    for fleet_id, split in sorted(split_by_fleet.items()):
        emergencies = [
            order for order in orders.orders
            if order is not split and _is_emergency_fleet_recovery_order(order, fleet_id)
        ]
        if emergencies:
            removed.add(id(split))
            decisions.append({
                "fleet_id": fleet_id,
                "outcome": "recovery_wins",
                "suppressed_order_kinds": [],
                "reason": "A genuine fleet-recovery operation takes precedence over experimental scout splitting.",
            })
            continue
        suppressed = []
        for order in orders.orders:
            if order is split or order.kind not in _STACK_ROUTINE_ORDER_KINDS:
                continue
            try:
                applies = int((order.payload or {}).get("fleet_id", -1)) == fleet_id
            except (TypeError, ValueError):
                applies = False
            if applies:
                removed.add(id(order))
                suppressed.append(order.kind)
        decisions.append({
            "fleet_id": fleet_id,
            "outcome": "split_wins",
            "suppressed_order_kinds": sorted(suppressed),
            "reason": (
                "Safe pure-scout stack is split before any routine route; singleton routing waits for next M-file read-back."
                if split.kind == "split_scout_fleet"
                else "Stacked hulls are split before any routine route; each ship is tasked after the next M-file read-back."
            ),
        })

    if removed:
        orders.orders = [order for order in orders.orders if id(order) not in removed]
        # A recovery exception removes the semantic split as well.  Keep the
        # early-planning marker truthful for diagnostics and for callers that
        # inspect this state after the final barrier.
        remaining_scout_fleets=[]
        remaining_split_fleets=[]
        for order in orders.orders:
            if order.kind not in _SPLIT_ORDER_KINDS:
                continue
            try:
                fleet_id=int((order.payload or {}).get("fleet_id", -1))
            except (TypeError, ValueError):
                continue
            remaining_split_fleets.append(fleet_id)
            if order.kind == "split_scout_fleet":
                remaining_scout_fleets.append(fleet_id)
        state.native["pending_scout_split_fleet_ids"] = sorted(set(remaining_scout_fleets))
        state.native["pending_split_fleet_ids"] = sorted(set(remaining_split_fleets))
        diagnostics=(state.native or {}).get("scout_stack_diagnostics")
        if isinstance(diagnostics, dict):
            diagnostics["split_orders"] = len(state.native["pending_scout_split_fleet_ids"])
    if decisions:
        state.native["scout_split_precedence"] = decisions
        for decision in decisions:
            if decision["outcome"] == "split_wins":
                orders.notes.append(
                    f"FLEET SPLIT PRECEDENCE: fleet {decision['fleet_id']} split wins; suppressed "
                    f"{', '.join(decision['suppressed_order_kinds']) or 'no'} routine route(s)."
                )
            else:
                orders.notes.append(
                    f"SCOUT SPLIT PRECEDENCE: fleet {decision['fleet_id']} recovery wins; split deferred."
                )
    return decisions


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


def _fleet_is_threatened(state: GameState, fleet: Any) -> bool:
    """Whether a foreign fleet is already on top of this fleet."""
    for other in state.fleets:
        try:
            owner = int(getattr(other, "owner", -1))
        except (TypeError, ValueError):
            continue
        if owner <= 0 or owner == int(state.player_id):
            continue
        if (
            abs(float(other.position.x) - float(fleet.position.x)) <= 1.0
            and abs(float(other.position.y) - float(fleet.position.y)) <= 1.0
        ):
            return True
    return False


def _planet_is_threatened(state: GameState, planet: Any) -> bool:
    """Whether a foreign fleet is at this world."""
    for fleet in state.fleets:
        try:
            owner = int(getattr(fleet, "owner", -1))
        except (TypeError, ValueError):
            continue
        if owner <= 0 or owner == int(state.player_id):
            continue
        native = getattr(fleet, "native", {}) or {}
        try:
            if int(native.get("position_object_id", -1) or -1) == int(planet.id):
                return True
        except (TypeError, ValueError):
            pass
        if (
            abs(float(fleet.position.x) - float(planet.position.x)) <= 1.0
            and abs(float(fleet.position.y) - float(planet.position.y)) <= 1.0
        ):
            return True
    return False


def _plan_scout_dispersion(state: GameState, orders: OrderSet) -> list[dict[str, Any]]:
    """Split stationary empty stacks so each hull can take its own route.

    Scouts, freighters, colony ships, miners, and every other non-battle hull
    are split whenever two or more copies share a fleet. Battle hulls, including
    fighters, are split only while no enemy is on top of them. A stack that is
    loaded, already routed, or recovering stays intact. One design slot owns
    the source fleet for this turn.
    """
    roles = _design_roles(state)
    scout_slots = {slot for slot, role in roles.items() if role == "scout"}
    existing_split_fleets: set[int] = set()
    for order in orders.orders:
        if order.kind not in _SPLIT_ORDER_KINDS:
            continue
        try:
            existing_split_fleets.add(int((order.payload or {}).get("fleet_id", -1)))
        except (TypeError, ValueError):
            continue
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
            stacked.append({
                "fleet_id": int(fleet.id),
                "scout_slot": slot,
                "ship_count": count,
                "wasted_survey_capacity": wasted,
                "pure_scout_stack": non_scout_total == 0 and scout_total == count,
            })

        if int(fleet.id) in existing_split_fleets:
            continue
        if _has_cargo(fleet) or not _is_stationary_for_scout_split(fleet):
            continue
        if not 0 <= int(fleet.id) <= 0x1FF:
            continue
        if any(_is_emergency_fleet_recovery_order(order, int(fleet.id)) for order in orders.orders):
            continue

        choices: list[tuple[str, int, int]] = []
        for slot, count in enumerate(counts):
            if count <= 1:
                continue
            organization = _slot_organization(roles.get(slot, ""), fleet)
            if organization == "leave":
                continue
            if organization == "battle" and _fleet_is_threatened(state, fleet):
                continue
            choices.append((organization, slot, count))
        if not choices:
            continue
        scouts = [choice for choice in choices if roles.get(choice[1], "") == "scout"]
        organization, slot, count = max(scouts or choices, key=lambda choice: choice[2])
        kind = "split_scout_fleet" if roles.get(slot, "") == "scout" else "split_fleet"
        mission = (
            "split_scouts_for_parallel_survey"
            if kind == "split_scout_fleet"
            else "split_battle_hulls_for_independent_tasks"
            if organization == "battle"
            else "split_stack_for_parallel_routes"
        )
        payload = {
            "fleet_id": int(fleet.id),
            "design_slot": slot,
            "scout_slot": slot,
            "ship_count": count,
            "singleton_fleets_required": count - 1,
            "mission": mission,
            "native_experiment": {
                "enabled": True,
                "trust_level": "CAPTURED_LAYOUT",
                "id": "type24-type23-singleton-split",
                "risk": (
                    "Emits the captured Type24/Type23 pair once per excess hull. "
                    "The source fleet is not also given a route this turn."
                ),
            },
        }
        orders.add(
            kind,
            payload,
            (
                f"Split stacked fleet {fleet.id}: design slot {slot} has {count} ships. "
                f"Create {count - 1} singleton fleet(s) so each hull can take its own route."
            ),
            priority=140,
        )
        decisions.append(payload)
        existing_split_fleets.add(int(fleet.id))

    scout_ids = []
    split_ids = []
    for order in orders.orders:
        if order.kind not in _SPLIT_ORDER_KINDS:
            continue
        try:
            fleet_id = int((order.payload or {}).get("fleet_id", -1))
        except (TypeError, ValueError):
            continue
        split_ids.append(fleet_id)
        if order.kind == "split_scout_fleet":
            scout_ids.append(fleet_id)
    state.native["pending_scout_split_fleet_ids"] = sorted(set(scout_ids))
    state.native["pending_split_fleet_ids"] = sorted(set(split_ids))
    state.native["scout_stack_diagnostics"] = {
        "scout_slots": sorted(scout_slots),
        "stacked_fleet_count": len({item["fleet_id"] for item in stacked}),
        "wasted_survey_capacity": wasted_total,
        "stacked_fleets": stacked,
        "split_orders": len(set(scout_ids)),
    }
    scout_decisions = [item for item in decisions if item["mission"] == "split_scouts_for_parallel_survey"]
    if stacked and scout_decisions:
        orders.notes.append(
            f"SCOUT STACK DIAGNOSTIC: {wasted_total} wasted survey capacity across "
            f"{len({item['fleet_id'] for item in stacked})} stacked scout fleet(s); "
            f"queued {len(scout_decisions)} safe singleton-split order(s)."
        )
    other_decisions = [item for item in decisions if item not in scout_decisions]
    if other_decisions:
        orders.notes.append(
            f"FLEET STACK SPLIT: queued {len(other_decisions)} split(s) for stacked "
            "cargo, colony, miner, fighter, or other hulls."
        )
    return decisions


def prepare_scout_split_precedence(state: GameState, orders: OrderSet) -> list[dict[str, Any]]:
    """Queue safe scout splits before planners can assign ordinary routes.

    The final :func:`enforce_scout_split_precedence` call remains mandatory:
    planners outside exploration may still add a source-fleet operation later
    in the turn.  This early pass, however, publishes the pending source IDs
    so normal recon planning never tries to route a fleet whose child IDs do
    not exist until the host returns the next M file.
    """
    return _plan_scout_dispersion(state, orders)


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
    prepare_scout_split_precedence(state, orders)
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
        anchor = anchors[planet_id]
        if not _planet_is_threatened(state, anchor):
            continue
        retained = max(fleets, key=lambda fleet: (_combat_value(fleet), -int(fleet.id)))
        sources = sorted(int(fleet.id) for fleet in fleets if int(fleet.id) != int(retained.id))
        if not sources:
            continue
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

    queued_scout_split_count=sum(
        1 for order in orders.orders if order.kind=="split_scout_fleet"
    )
    state.native["fleet_battle_management"] = {
        "mode": "concentrate_idle_armed_fleets",
        "type37_merge_capability": "PARTIAL",
        "scout_singleton_splits": queued_scout_split_count,
        "merges": decisions,
        "battle_policy": policy,
        "safeguards": [
            "No automatic chaff program unless a game-specific host rule explicitly enables it.",
            "Stationary empty stacks of any non-battle hull are split so each ship can take its own route.",
            "Fighters and other battle hulls join only while an enemy is at that world; a quiet stack is split.",
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
