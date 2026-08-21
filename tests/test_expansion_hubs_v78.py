from types import SimpleNamespace

from stars_ai.base_network import evaluate_base_network
from stars_ai.fuel_planner import apply_fuel_safety
from stars_ai.memory import AgentMemory
from stars_ai.models import (
    Fleet,
    GameState,
    OrderSet,
    Planet,
    Position,
    RaceProfile,
    Tech,
)
from stars_ai.native.orders import parse_production_change
from stars_ai.native.x_writer import _build_decision_report, _production_block
from stars_ai.starbase_capabilities import starbase_capabilities
from stars_ai.starbase_planner import plan_support_base_builds
from stars_ai.strategy.economy import add_economic_orders


def _fleet_profile():
    return {
        "dry_mass": 65,
        "cargo_mass": 0,
        "mass": 65,
        "cargo_capacity": 25,
        "fuel": 200,
        "fuel_capacity": 200,
        "effective_fuel": 200,
        "at_starbase": True,
        "groups": [{
            "design_slot": 1,
            "count": 1,
            "mass": 65,
            "engine_id": 4,
            "engine_name": "Daddy Long Legs 7",
            "design_name": "Mayflower",
        }],
    }


def _opening_state(population=25_000):
    home = Planet(
        0, "Home", Position(0, 0), owner=1, observed=True,
        habitability=100, population=population, germanium=200,
        native={
            "position_object_id": 0,
            "has_starbase": True,
            "starbase_design": 0,
            "starbase_capabilities": starbase_capabilities(34),
        },
    )
    green = Planet(
        1, "Green", Position(80, 0), owner=None, observed=True,
        habitability=80, native={"mineral_concentrations": [50, 50, 50]},
    )
    colony = Fleet(
        0, "Mayflower", 1, Position(0, 0), role="colony",
        cargo_population=0, speed=8,
        native={
            "position_object_id": 0,
            "fuel_profile": _fleet_profile(),
            "race_fuel_flags": {"ife": False, "ce": False},
        },
    )
    return GameState(
        "g", 2404, 1, RaceProfile(), Tech(), [home, green], [colony],
        native={
            "design_profiles": [],
            "starbase_profiles": [{
                "design_number": 0,
                "name": "Starbase",
                "hull_id": 34,
                "hull_name": "Space Station",
                "capabilities": starbase_capabilities(34),
            }],
            "production_by_planet": {},
            "strategic_watchdog": {"new_colonies": 0, "colonization_pressure": 2.0},
        },
    )


def _hub_state(production=None):
    hub = Planet(
        0, "Core", Position(0, 0), owner=1, population=100_000,
        native={
            "has_starbase": True,
            "starbase_design": 0,
            "starbase_capabilities": starbase_capabilities(34),
        },
    )
    fort = Planet(
        1, "Frontier Fort", Position(240, 0), owner=1, population=20_000,
        ironium=2_000, boranium=2_000, germanium=2_000,
        native={
            "has_starbase": True,
            "starbase_design": 1,
            "starbase_capabilities": starbase_capabilities(32),
        },
    )
    green = Planet(
        2, "Green", Position(260, 0), owner=None, observed=True, habitability=75,
    )
    return GameState(
        "g", 2405, 1, RaceProfile(), Tech(), [hub, fort, green], [],
        native={
            "design_profiles": [],
            "starbase_profiles": [{
                "design_number": 0,
                "name": "Starbase",
                "hull_id": 34,
                "hull_name": "Space Station",
                "capabilities": starbase_capabilities(34),
            }, {
                "design_number": 1,
                "name": "Orbital Fort",
                "hull_id": 32,
                "hull_name": "Orbital Fort",
                "capabilities": starbase_capabilities(32),
            }],
            "production_by_planet": dict(production or {}),
        },
    )


def test_25kt_colony_load_moves_2500_and_leaves_22500_on_source():
    state = _opening_state(25_000)
    orders = OrderSet("g", state.year, 1)

    add_economic_orders(state, orders)

    colony = next(order for order in orders.orders if order.kind == "colony_operation")
    assert colony.payload["load_25kt_population"] is True
    assert colony.payload["load_population_kt"] == 25
    assert colony.payload["population"] == 2_500
    assert colony.payload["source_population"] == 25_000
    assert colony.payload["source_population_after_load"] == 22_500
    assert colony.payload["source_population_reserve"] == 10_000
    assert colony.payload["minimum_launch_population"] == 12_500

    report = _build_decision_report(
        state,
        orders,
        SimpleNamespace(fleet_intents=[], memory=AgentMemory()),
        [{"kind": colony.kind, "payload": colony.payload, "reason": colony.reason}],
        [],
        [],
    )
    assert "YES - native 25 kT instruction; expected transfer=2,500 colonists" in report
    assert "source population=25000; source after load=22500" in report
    assert "race-adjusted hab current=80%; current-tech terraform=80%; eventual terraform=80%; planning value=80%" in report
    assert "policy=opening_quality floor=60%" in report
    assert "basis=racial_habitability" in report


def test_opening_colony_keeps_minimum_living_source_population():
    state = _opening_state(12_499)
    orders = OrderSet("g", state.year, 1)

    add_economic_orders(state, orders)

    assert not any(order.kind == "colony_operation" for order in orders.orders)


def test_partially_loaded_colony_only_deducts_remaining_capacity():
    state = _opening_state(25_000)
    state.fleets[0].cargo_population = 1_000
    orders = OrderSet("g", state.year, 1)

    add_economic_orders(state, orders)

    colony = next(order for order in orders.orders if order.kind == "colony_operation")
    assert colony.payload["native_load_order_kt"] == 25
    assert colony.payload["load_population_kt"] == 15
    assert colony.payload["population_loaded"] == 1_500
    assert colony.payload["population"] == 2_500
    assert colony.payload["source_population_after_load"] == 23_500


def test_2500_colonists_already_aboard_does_not_reload():
    state = _opening_state(25_000)
    state.fleets[0].cargo_population = 2_500
    orders = OrderSet("g", state.year, 1)

    add_economic_orders(state, orders)

    colony = next(order for order in orders.orders if order.kind == "colony_operation")
    assert colony.payload["load_25kt_population"] is False
    assert colony.payload["population"] == 2_500
    assert colony.payload["load_decision"] == "already_loaded"


def test_colony_fuel_safety_includes_the_planned_25kt_load():
    state = _opening_state(25_000)
    state.planets[1].position = Position(400, 0)
    orders = OrderSet("g", state.year, 1)
    orders.add(
        "colony_operation",
        {
            "fleet_id": 0,
            "destination_planet_id": 1,
            "warp": 7,
            "mission": "colonize",
            "load_25kt_population": True,
        },
        "test",
        priority=100,
    )

    apply_fuel_safety(state, orders)

    operation = next(order for order in orders.orders if order.kind == "colony_operation")
    assert operation.payload["warp"] == 6
    assert operation.payload["fuel_plan"]["mass"] == 90


def test_remote_orbital_fort_becomes_a_concrete_fuel_hub_build():
    state = _hub_state()

    requests = plan_support_base_builds(state)
    recommendation = next(x for x in evaluate_base_network(state) if x.planet_id == 1)

    assert len(requests) == 1
    assert requests[0].planet_id == 1
    assert requests[0].design_slot == 0
    assert "refueling" in requests[0].reason
    assert recommendation.role == "FUEL_HUB"
    assert recommendation.build_or_upgrade is True

    orders = OrderSet("g", state.year, 1)
    add_economic_orders(state, orders)
    queue = next(
        order.payload["queue"]
        for order in orders.orders
        if order.kind == "set_planet_queue" and order.payload["planet_id"] == 1
    )
    # Development comes first; the ready support base remains in the same
    # queue rather than starving mines/factories on its host world.
    assert queue[0]["item"] in {"mine", "factory"}
    base = next(item for item in queue if item["item"] == "starbase_design")
    assert base["design_slot"] == 0


def test_native_starbase_queue_uses_offset_slot_and_preserves_completion():
    block = _production_block(1, [{
        "item": "starbase_design",
        "design_slot": 0,
        "quantity": 1,
        "complete_percent": 37,
    }])

    assert block.data == bytes.fromhex("01 00 01 40 54 02")
    decoded = parse_production_change(block.data)
    assert decoded.items[0].item_id == 16
    assert decoded.items[0].item_name == "StarbaseDesignSlot#0"
    assert decoded.items[0].complete_percent == 37

    state = _hub_state({
        "1": [{
            "item_id": 16,
            "item_type": 4,
            "count": 1,
            "complete_percent": 37,
            "item_name": "StarbaseDesignSlot#0",
        }],
    })
    request = plan_support_base_builds(state)[0]
    assert request.planet_id == 1
    assert request.complete_percent == 37


def test_starbase_production_expectation_completes_from_queue_or_upgrade():
    emitted = [{
        "kind": "set_planet_queue",
        "payload": {
            "planet_id": 1,
            "queue": [{"item": "starbase_design", "design_slot": 0, "quantity": 1}],
        },
    }]

    queued = AgentMemory()
    queued.record_emitted_actions(emitted, _hub_state())
    queued_state = _hub_state({
        "1": [{"item_id": 16, "item_type": 4, "count": 1,
               "complete_percent": 10, "item_name": "StarbaseDesignSlot#0"}],
    })
    queued_state.year = 2406
    assert queued.evaluate_action_outcomes(queued_state)[0]["status"] == "COMPLETED"

    upgraded = AgentMemory()
    upgraded.record_emitted_actions(emitted, _hub_state())
    upgraded_state = _hub_state()
    upgraded_state.year = 2406
    upgraded_state.planets[1].native.update({
        "starbase_design": 0,
        "starbase_capabilities": starbase_capabilities(34),
    })
    assert upgraded.evaluate_action_outcomes(upgraded_state)[0]["status"] == "COMPLETED"
