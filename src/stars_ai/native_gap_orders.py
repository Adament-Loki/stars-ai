"""Planner coverage for native commands that now have an encoder.

Each order is narrow on purpose. A fleet is scrapped only when it is an
extra empty freighter already at an owned starbase. Patrol is a task-7
waypoint plus a repeat-orders flag. Battle plans are pulled back to the
primary plan. Research-only mode is cleared when a world is locked to
leftover research. Bombers move to a hostile colony and bomb on arrival.
"""
from __future__ import annotations

from .models import GameState, OrderSet


def _owned_planets(state: GameState) -> list:
    return [planet for planet in state.planets if planet.owner == state.player_id]


def _at_planet(fleet, planet) -> bool:
    native = fleet.native or {}
    if int(native.get("position_object_id", -1) or -1) == int(planet.id):
        return True
    return (
        abs(float(fleet.position.x) - float(planet.position.x)) <= 0.5
        and abs(float(fleet.position.y) - float(planet.position.y)) <= 0.5
    )


def _has_starbase(planet) -> bool:
    native = planet.native or {}
    if native.get("has_starbase") is True:
        return True
    capabilities = native.get("starbase_capabilities") or {}
    return bool(capabilities.get("can_refuel") or capabilities.get("can_build_ships"))


def _idle(fleet) -> bool:
    native = fleet.native or {}
    if fleet.destination_planet_id is not None:
        return False
    if native.get("native_destination_fleet_id") is not None:
        return False
    return int(native.get("waypoint_count", 1) or 1) <= 1


def _empty(fleet) -> bool:
    cargo = (fleet.native or {}).get("cargo") or {}
    if any(int(cargo.get(key, 0) or 0) > 0 for key in ("ironium", "boranium", "germanium", "population")):
        return False
    return int(getattr(fleet, "cargo_population", 0) or 0) == 0


_EXCLUSIVE = {"scrap_fleet", "move_fleet", "bomb_orbit", "colony_operation", "transport_population", "transport_minerals"}


def _already(orders: OrderSet, fleet_id: int) -> bool:
    """True when this fleet already has an order that consumes its waypoint."""
    return any(
        order.kind in _EXCLUSIVE and int(order.payload.get("fleet_id", -1)) == int(fleet_id)
        for order in orders.orders
    )


def add_native_gap_orders(state: GameState, orders: OrderSet, plan=None) -> None:
    owned = _owned_planets(state)

    freighters = [
        fleet for fleet in state.fleets
        if fleet.owner == state.player_id and fleet.role == "freighter" and _empty(fleet) and _idle(fleet)
    ]
    if len(freighters) >= 2:
        anchored = []
        for fleet in freighters:
            planet = next((item for item in owned if _at_planet(fleet, item) and _has_starbase(item)), None)
            if planet is not None and not _already(orders, fleet.id):
                anchored.append((fleet, planet))
        if len(anchored) >= 2:
            fleet, planet = sorted(anchored, key=lambda item: int(item[0].id))[-1]
            orders.add(
                "scrap_fleet",
                {"fleet_id": int(fleet.id), "destination_planet_id": int(planet.id)},
                f"Scrap extra empty freighter {fleet.name} at {planet.name}; another empty freighter remains.",
                priority=40,
            )

    for fleet in state.fleets:
        if fleet.owner != state.player_id or _already(orders, fleet.id):
            continue
        plan_index = (fleet.native or {}).get("battle_plan")
        if plan_index is None or int(plan_index) == 0:
            continue
        if fleet.role not in {"combat", "bomber", "escort"}:
            continue
        orders.add(
            "set_battle_plan",
            {"fleet_id": int(fleet.id), "plan_index": 0},
            f"Return {fleet.name} to the primary battle plan.",
            priority=70,
        )

    for planet in owned:
        flag = (planet.native or {}).get("contribute_only_leftover_resources_to_research")
        if flag is not True:
            continue
        if any(int(order.payload.get("planet_id", -1)) == int(planet.id) and order.kind == "set_planet_research_mode" for order in orders.orders):
            continue
        orders.add(
            "set_planet_research_mode",
            {"planet_id": int(planet.id), "leftover_only": False},
            f"Clear research-only mode at {planet.name} so its production queue can spend resources.",
            priority=58,
        )

    idle_combat = [
        fleet for fleet in state.fleets
        if fleet.owner == state.player_id
        and fleet.role in {"combat", "escort"}
        and _idle(fleet)
        and not _already(orders, fleet.id)
    ]
    for fleet in idle_combat[:1]:
        here = next((planet for planet in owned if _at_planet(fleet, planet)), None)
        if here is None:
            continue
        others = [planet for planet in owned if int(planet.id) != int(here.id)]
        if not others:
            continue
        target = min(others, key=lambda planet: (planet.position.x - fleet.position.x) ** 2 + (planet.position.y - fleet.position.y) ** 2)
        distance = ((target.position.x - fleet.position.x) ** 2 + (target.position.y - fleet.position.y) ** 2) ** 0.5
        if distance > 81:
            continue
        orders.add(
            "move_fleet",
            {
                "fleet_id": int(fleet.id),
                "destination_planet_id": int(target.id),
                "warp": 6,
                "mission": "patrol",
            },
            f"Patrol {fleet.name} between {here.name} and {target.name}.",
            priority=48,
        )
        orders.add(
            "repeat_fleet_orders",
            {"fleet_id": int(fleet.id), "enabled": True},
            f"Repeat the patrol orders for {fleet.name}.",
            priority=47,
        )

    hostile_ids = set()
    if plan is not None:
        for player_id, view in (getattr(plan, "diplomacy", {}) or {}).items():
            if str(view.get("attitude")) == "hostile" and not view.get("is_human"):
                hostile_ids.add(int(player_id))
    role_names = {
        "scout": "Scout",
        "colony": "Colony",
        "freighter": "Freighter",
        "miner": "Miner",
        "combat": "Guard",
        "bomber": "Bomber",
        "minelayer": "Minelayer",
        "tender": "Tender",
    }
    named = [
        fleet for fleet in state.fleets
        if fleet.owner == state.player_id and fleet.role in role_names
    ]
    if named and not any(order.kind == "rename_fleet" for order in orders.orders):
        fleet = min(named, key=lambda item: int(item.id))
        orders.add(
            "rename_fleet",
            {"fleet_id": int(fleet.id), "name": role_names[fleet.role]},
            f"Name fleet {fleet.id} {role_names[fleet.role]} so the stack is identifiable in the client.",
            priority=30,
        )

    bombers = [
        fleet for fleet in state.fleets
        if fleet.owner == state.player_id and fleet.role == "bomber" and _idle(fleet) and not _already(orders, fleet.id)
    ]
    targets = [
        planet for planet in state.planets
        if planet.owner in hostile_ids and int(planet.population or 0) > 0
    ]
    if bombers and targets:
        fleet = bombers[0]
        target = min(targets, key=lambda planet: (planet.position.x - fleet.position.x) ** 2 + (planet.position.y - fleet.position.y) ** 2)
        orders.add(
            "bomb_orbit",
            {
                "fleet_id": int(fleet.id),
                "destination_planet_id": int(target.id),
                "warp": 6,
                "mission": "bomb",
            },
            f"Send {fleet.name} to bomb {target.name}. Stars! bombs after arrival; the waypoint task stays move.",
            priority=90,
        )
