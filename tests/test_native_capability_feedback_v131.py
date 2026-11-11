from types import SimpleNamespace

from stars_ai.capability_execution import (
    BLOCKED_NATIVE,
    EMITTED,
    HOST_CONFIRMED,
    NATIVE_READY,
    PLANNED,
)
from stars_ai.design_development import add_design_development_orders, plan_design_development
from stars_ai.memory import AgentMemory
from stars_ai.models import Fleet, GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.objective_production import plan_objective_ship_builds
from stars_ai.persona import BalancedPersona
from stars_ai.starbase_capabilities import starbase_capabilities
from stars_ai.starbase_planner import plan_support_base_builds


def _lifecycle_state():
    return SimpleNamespace(year=2420, native={"design_profiles": [], "starbase_profiles": []})


def _blocked_entry(capability_id, role, slot, *, is_starbase=False, purpose=None):
    return {
        "capability_id": capability_id,
        "role": role,
        "purpose": purpose,
        "is_starbase": is_starbase,
        "target_slot": slot,
        "status": BLOCKED_NATIVE,
        "blocking_reason": "native writer rejected this Type-27 request",
    }


def _starbase_state():
    home = Planet(
        0, "Home", Position(0, 0), owner=1, population=180_000,
        ironium=2_000, boranium=2_000, germanium=2_000,
        native={
            "is_homeworld": True,
            "has_starbase": True,
            "starbase_design": 0,
            "starbase_capabilities": starbase_capabilities(32),
        },
    )
    frontier = Planet(
        1, "Frontier", Position(180, 0), owner=1, population=70_000,
        ironium=1_500, boranium=1_500, germanium=1_500,
    )
    return GameState(
        "capability-feedback", 2420, 1,
        RaceProfile(native={"prt_id": 3}), Tech(construction=8, weapons=6, energy=6),
        [home, frontier], [], native={
            "designs": [{
                "design_number": 0, "name": "Orbital Fort", "hull_id": 32,
                "is_starbase": True, "total_remaining": 0, "slots": [],
            }],
            "design_profiles": [],
            "starbase_profiles": [{
                "design_number": 0, "name": "Orbital Fort", "hull_id": 32,
                "hull_name": "Orbital Fort", "capabilities": starbase_capabilities(32),
                "slots": [],
            }],
            "production_by_planet": {},
        },
    )


def _territorial_state():
    home = Planet(0, "Home", Position(0, 0), owner=1, population=200_000)
    frontier = Planet(1, "Frontier", Position(110, 0), owner=1, population=80_000)
    hostile_world = Planet(
        3, "Lost Frontier", Position(85, 0), owner=2, population=60_000,
        native={"recently_lost_by_player": True},
    )
    intruder = Fleet(
        9, "Foreign Convoy", 2, Position(80, 0), role="unknown", combat_power=30,
        native={"cargo": {"population": 80}},
    )
    return GameState(
        "capability-territory", 2420, 1, RaceProfile(native={"prt_id": 3}),
        Tech(weapons=6, energy=6, propulsion=4, construction=6),
        [home, frontier, hostile_world], [intruder], native={
            # These stale records deliberately model the dangerous state: a
            # writer-rejected creation must not be considered buildable merely
            # because an incomplete/ghost profile was retained by an adapter.
            "design_profiles": [
                {"design_number": 4, "name": "Ghost Mine Layer", "role": "minelayer", "hull_id": 5},
                {"design_number": 5, "name": "Ghost Patrol", "role": "combat", "hull_id": 5},
                {"design_number": 6, "name": "Ghost Bomber", "role": "bomber", "hull_id": 5},
                {"design_number": 7, "name": "Real Scout", "role": "scout", "hull_id": 4},
            ],
            "production_by_planet": {},
            "objects": [],
        },
    )


def test_capability_lifecycle_records_writer_outcomes_and_host_readback():
    memory = AgentMemory()
    state = _lifecycle_state()
    orders = OrderSet("feedback", 2420, 1)
    payload = {"role": "combat", "name": "Border Combat", "target_slot": 3}
    orders.add("create_ship_design", payload, "Need combat capability.")

    memory.record_capability_plans(orders, state)
    entry = memory.capability_execution["design:combat"]
    assert entry["status"] == PLANNED

    memory.record_native_capability_execution(
        [{"kind": "create_ship_design", "payload": payload, "reason": "encoded"}], [], state,
    )
    entry = memory.capability_execution["design:combat"]
    assert entry["status"] == EMITTED
    assert [event["status"] for event in entry["status_history"]] == [
        PLANNED, NATIVE_READY, EMITTED,
    ]

    state.native["design_profiles"] = [{
        "design_number": 3, "name": "Border Combat", "role": "combat",
    }]
    result = memory.reconcile_capability_execution(state)
    assert result["host_confirmed_capability_ids"] == ["design:combat"]
    assert memory.capability_execution["design:combat"]["status"] == HOST_CONFIRMED


def test_blocked_starbase_creation_stays_unavailable_to_design_and_production():
    state = _starbase_state()
    state.native["capability_execution"] = {
        "design:starbase:support": _blocked_entry(
            "design:starbase:support", "starbase", 1, is_starbase=True, purpose="support",
        ),
    }
    orders = OrderSet(state.game_name, state.year, state.player_id)

    add_design_development_orders(state, orders, BalancedPersona().build_plan(state))

    assert not any(
        order.kind in {"create_design", "create_ship_design"}
        and bool(order.payload.get("is_starbase", False))
        for order in orders.orders
    )
    assert any(
        item["capability_id"] == "design:starbase:support"
        for item in state.native["open_design_capability_demand"]
    )
    assert plan_support_base_builds(state, BalancedPersona().build_plan(state)) == []
    assert state.native["starbase_capability_demand"]["status"] == BLOCKED_NATIVE


def test_blocked_design_yields_to_next_legal_strategic_capability():
    state = _starbase_state()
    state.fleets.append(Fleet(
        9, "Border Intruder", 2, Position(80, 0), role="combat", combat_power=40,
    ))
    state.native["capability_execution"] = {
        "design:starbase:support": _blocked_entry(
            "design:starbase:support", "starbase", 1, is_starbase=True, purpose="support",
        ),
    }
    orders = OrderSet(state.game_name, state.year, state.player_id)

    add_design_development_orders(state, orders, BalancedPersona().build_plan(state))

    assert any(
        order.kind in {"create_design", "create_ship_design"}
        and order.payload.get("role") == "combat"
        for order in orders.orders
    )
    assert not any(
        bool(order.payload.get("is_starbase", False))
        for order in orders.orders if order.kind in {"create_design", "create_ship_design"}
    )


def test_blocked_combat_bomber_and_minelayer_demands_remain_open_without_hulls():
    state = _territorial_state()
    state.native["capability_execution"] = {
        "design:combat": _blocked_entry("design:combat", "combat", 5),
        "design:bomber": _blocked_entry("design:bomber", "bomber", 6),
        "design:minelayer": _blocked_entry("design:minelayer", "minelayer", 4),
    }
    plan = BalancedPersona().build_plan(state)

    proposals = plan_design_development(state, plan)
    demand = state.native["proactive_force_capability_demand"]

    assert demand["combat_seed_required"]
    assert not demand["bomber_design_required"]
    assert demand["minelayer_design_required"]
    assert {item["capability_id"] for item in state.native["open_design_capability_demand"]} >= {
        "design:combat", "design:minelayer",
    }
    assert "design:bomber" not in {
        item["capability_id"] for item in state.native["open_design_capability_demand"]
    }
    assert not any(proposal.role in {"combat", "bomber", "minelayer"} for proposal in proposals)

    builds = plan_objective_ship_builds(state, plan)
    assert not any(build.role in {"combat", "bomber", "minelayer"} for build in builds)
    assert state.native["minelayer_capability_demand"]["blocked_native"]
    assert state.native["bomber_capability_demand"]["blocked_native"]
    assert state.native["objective_combat_policy"]["blocked_native"]


def test_duplicate_architecture_is_retried_instead_of_blocking_the_role():
    memory = AgentMemory()
    state = _lifecycle_state()
    orders = OrderSet("feedback", 2444, 2)
    payload = {
        "role": "combat", "is_starbase": False, "purpose": "combat",
        "name": "Fleet Combat Mk II", "target_slot": 8,
    }
    orders.add("create_design", payload, "Weapons improved.")
    memory.record_capability_plans(orders, state)
    memory.record_native_capability_execution([], [{
        "kind": "create_design",
        "payload": payload,
        "reason": (
            "Generic proposal cannot be compiled into a client-safe ship design: "
            "the requested hull/engine/components are not all researched and race-legal, "
            "the architecture already exists, or it is an unvalidated starbase mutation."
        ),
    }], state)

    entry = memory.capability_execution["design:combat"]
    assert entry["status"] == PLANNED
    assert "blocking_reason" not in entry


def test_writer_rejection_persists_blocked_status_with_reason(tmp_path):
    memory = AgentMemory()
    state = _lifecycle_state()
    orders = OrderSet("feedback", 2420, 1)
    payload = {
        "role": "starbase", "is_starbase": True, "purpose": "support",
        "name": "Fleet Support", "target_slot": 1,
    }
    orders.add("create_design", payload, "Need a support base.")
    memory.record_capability_plans(orders, state)
    memory.record_native_capability_execution([], [{
        "kind": "create_design", "payload": payload,
        "reason": "no safe same-hull client seed exists",
    }], state)

    entry = memory.capability_execution["design:starbase:support"]
    assert entry["status"] == BLOCKED_NATIVE
    assert "same-hull client seed" in entry["blocking_reason"]
    assert [event["status"] for event in entry["status_history"]] == [
        PLANNED, BLOCKED_NATIVE,
    ]
    path = tmp_path / "memory.json"
    memory.save(path)
    restored = AgentMemory.load(path)
    assert restored.capability_execution["design:starbase:support"]["status"] == BLOCKED_NATIVE
