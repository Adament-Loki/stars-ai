from stars_ai.colony_planner import score_colony_candidates
from stars_ai.exploration_router import build_probe_route
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
from stars_ai.native.x_writer import _encode_queue_item, _production_block
from stars_ai.objective_production import _desired_colony_force
from stars_ai.objective_production import plan_objective_ship_builds
from stars_ai.strategic_watchdog import _next_milestone
from stars_ai.strategy.economy import add_economic_orders
from stars_ai.strategy.exploration import add_exploration_orders
from stars_ai.terraforming import evaluate_terraforming, terraforming_limits


def _race(lrts=None):
    return RaceProfile(native={
        "lrts": list(lrts or []),
        "hab_center": [50, 50, 50],
        "hab_low": [30, 30, 30],
        "hab_high": [70, 70, 70],
        "hab_immune": [False, False, False],
        "universal_hab": False,
    })


def _world(pid, name, x, *, owner=None, observed=True, hab=80, env=None, pop=0):
    native = {"mineral_concentrations": [50, 50, 50]}
    if env is not None:
        native.update({"environment": list(env), "original_environment": list(env)})
    if owner == 1 and pid == 0:
        native["is_homeworld"] = True
        native["position_object_id"] = pid
    return Planet(
        pid, name, Position(x, 0), owner=owner, observed=observed,
        habitability=hab, population=pop, native=native,
    )


def _state(planets, fleets=None, *, year=2404, tech=None, race=None, watchdog=None):
    return GameState(
        "g", year, 1, race or _race(), tech or Tech(), planets, fleets or [],
        native={
            "design_profiles": [],
            "production_by_planet": {},
            "strategic_watchdog": watchdog or {
                "new_colonies": 0,
                "colonization_pressure": 1.75,
                "milestone": {"deadline_turn": 5, "new_colonies_optimal": 3},
            },
        },
    )


def test_standard_terraforming_values_current_tech_and_long_term_habitability():
    home = _world(0, "Home", 0, owner=1, hab=100, env=(50, 50, 50), pop=50_000)
    marginal = _world(1, "Marginal", 40, hab=-1, env=(29, 50, 50))
    state = _state([home, marginal], tech=Tech(propulsion=1, biotechnology=1))

    potential = evaluate_terraforming(state, marginal)

    assert potential.tech_limits == (3, 0, 0)
    assert potential.tech_environment == (32, 50, 50)
    assert potential.eventual_environment == (44, 50, 50)
    assert potential.tech_habitability > potential.current_habitability
    assert potential.eventual_habitability > potential.tech_habitability
    assert potential.planning_habitability >= 60

    fleet = Fleet(0, "Colony", 1, Position(0, 0), role="colony")
    ranked = score_colony_candidates(state, fleet)
    assert [candidate.planet_id for candidate in ranked] == [1]
    assert ranked[0].current_habitability == -1
    assert ranked[0].eventual_terraform_habitability > 80


def test_total_terraforming_uses_biotechnology_only():
    state = _state(
        [_world(0, "Home", 0, owner=1, pop=50_000)],
        tech=Tech(biotechnology=9), race=_race(["TT"]),
    )
    assert terraforming_limits(state) == (10, 10, 10)
    assert terraforming_limits(state, eventual=True) == (30, 30, 30)


def test_colony_scoring_prefers_home_region_even_if_fleet_is_already_far_out():
    home = _world(0, "Home", 0, owner=1, pop=100_000)
    near = _world(1, "Near", 30)
    far = _world(2, "Far", 160)
    fleet = Fleet(0, "Colony", 1, Position(160, 0), role="colony")
    state = _state([home, near, far], [fleet])

    ranked = score_colony_candidates(state, fleet)

    assert ranked[0].planet_name == "Near"
    assert ranked[0].distance_from_homeworld == 30


def test_in_flight_colony_claim_is_reserved_and_idle_ships_fan_out():
    home = _world(0, "Home", 0, owner=1, pop=30_000)
    targets = [_world(i, name, i * 20) for i, name in enumerate(("A", "B", "C"), 1)]
    fleets = [
        Fleet(10, "Committed", 1, Position(10, 0), role="colony", destination_planet_id=1),
        Fleet(11, "Idle One", 1, Position(0, 0), role="colony", native={"position_object_id": 0}),
        Fleet(12, "Idle Two", 1, Position(0, 0), role="colony", native={"position_object_id": 0}),
    ]
    state = _state([home, *targets], fleets)
    orders = OrderSet("g", state.year, 1)

    add_economic_orders(state, orders)

    colony_orders = [o for o in orders.orders if o.kind == "colony_operation"]
    destinations = [o.payload["destination_planet_id"] for o in colony_orders]
    assert set(destinations) == {2, 3}
    assert len(destinations) == len(set(destinations))
    assert sorted(o.payload["source_population_after_load"] for o in colony_orders) == [25_000, 27_500]


def test_multiple_colony_loads_share_one_population_budget():
    home = _world(0, "Home", 0, owner=1, pop=14_000)
    targets = [_world(1, "A", 20), _world(2, "B", 40)]
    fleets = [
        Fleet(11, "Idle One", 1, Position(0, 0), role="colony", native={"position_object_id": 0}),
        Fleet(12, "Idle Two", 1, Position(0, 0), role="colony", native={"position_object_id": 0}),
    ]
    state = _state([home, *targets], fleets)
    orders = OrderSet("g", state.year, 1)

    add_economic_orders(state, orders)

    colony_orders = [o for o in orders.orders if o.kind == "colony_operation"]
    assert len(colony_orders) == 1
    assert colony_orders[0].payload["source_population_after_load"] == 11_500


def test_max_terraform_is_a_native_supported_production_item():
    assert _encode_queue_item("max_terraform", 1) == bytes.fromhex("01 14 02 00")
    decoded = parse_production_change(_production_block(0, [{
        "item": "max_terraform", "quantity": 1,
    }]).data)
    assert decoded.items[0].item_name == "Max Terraform (Auto Build)"

    planet = _world(0, "Home", 0, owner=1, hab=-1, env=(29, 50, 50), pop=100_000)
    state = _state([planet], tech=Tech(propulsion=1, biotechnology=1))
    orders = OrderSet("g", state.year, 1)
    add_economic_orders(state, orders)
    queue = next(o.payload["queue"] for o in orders.orders if o.kind == "set_planet_queue")
    assert queue[0]["item"] == "max_terraform"


def test_completed_terraforming_satisfies_production_command_status():
    before = _world(0, "Home", 0, owner=1, hab=-1, env=(29, 50, 50), pop=100_000)
    state = _state([before], tech=Tech(propulsion=1, biotechnology=1))
    memory = AgentMemory()
    memory.record_emitted_actions([{
        "kind": "set_planet_queue",
        "payload": {
            "planet_id": 0,
            "queue": [{"item": "max_terraform", "quantity": 1}],
        },
    }], state)

    after = _world(0, "Home", 0, owner=1, hab=20, env=(30, 50, 50), pop=100_000)
    next_state = _state([after], year=state.year + 1, tech=state.tech)

    outcome = memory.evaluate_action_outcomes(next_state)[0]
    assert outcome["status"] == "COMPLETED"


def test_observed_planet_with_old_intel_never_returns_to_scout_routes():
    home = _world(0, "Home", 0, owner=1, pop=50_000)
    old = _world(1, "Old", 20)
    old.native["intel_needs_refresh"] = True
    unknown = _world(2, "Unknown", 35, observed=False, hab=None)
    scout = Fleet(0, "Scout", 1, Position(0, 0), role="scout")
    state = _state([home, old, unknown], [scout])
    memory = AgentMemory()
    memory.set_scout_route(0, [1, 2], state.year, expected_discoveries=2, total_distance=35)

    memory.prune_scout_routes(state)
    assert memory.scout_route(0)["planet_ids"] == [2]

    orders = OrderSet("g", state.year, 1)
    add_exploration_orders(state, orders, memory=memory)
    scans = [o for o in orders.orders if o.payload.get("mission") == "scan"]
    assert scans and scans[0].payload["destination_planet_id"] == 2
    assert all(o.payload.get("destination_planet_id") != 1 for o in scans)


def test_probe_route_clears_close_home_region_before_distant_cluster():
    home = _world(0, "Home", 0, owner=1, pop=50_000)
    near = _world(1, "Near", 30, observed=False, hab=None)
    far = [
        _world(i, f"Far {i}", 145 + i * 5, observed=False, hab=None)
        for i in range(2, 6)
    ]
    scout = Fleet(0, "Scout", 1, Position(0, 0), role="scout")
    state = _state([home, near, *far], [scout])

    route = build_probe_route(state, scout, [near, *far], max_stops=5)

    assert route is not None
    assert route.planet_ids[0] == 1


def test_t25_milestone_and_colony_force_match_aggressive_expansion_goal():
    milestone = _next_milestone(25, 288)
    assert milestone["new_colonies_min"] == 13
    assert milestone["new_colonies_optimal"] == 20

    home = _world(0, "Home", 0, owner=1, pop=100_000)
    viable = [_world(i, f"P{i}", i * 10) for i in range(1, 13)]
    state = _state(
        [home, *viable], year=2410,
        watchdog={
            "new_colonies": 0,
            "colonization_pressure": 1.75,
            "milestone": {"deadline_turn": 10, "new_colonies_optimal": 7},
        },
    )
    desired, reason = _desired_colony_force(state, viable, None)
    assert desired >= 7
    assert "milestone needs 7 more colonies" in reason


def test_duplicate_active_claims_do_not_satisfy_pipeline_or_block_queue_top_up():
    home = _world(0, "Home", 0, owner=1, pop=100_000)
    home.native["starbase_capabilities"] = {"can_build_ships": True, "can_refuel": True}
    viable = [_world(i, f"P{i}", i * 20) for i in range(1, 5)]
    fleets = [
        Fleet(
            i, f"Duplicate {i}", 1, Position(0, 0), role="colony",
            destination_planet_id=1, native={"ship_count": [1]},
        )
        for i in range(1, 4)
    ]
    state = _state(
        [home, *viable], fleets, year=2410,
        watchdog={
            "new_colonies": 0,
            "colonization_pressure": 1.75,
            "milestone": {"deadline_turn": 10, "new_colonies_optimal": 7},
        },
    )
    state.native["design_profiles"] = [{
        "design_number": 0, "name": "Mayflower", "role": "colony",
        "dry_mass": 65, "fuel_capacity": 200, "engine_id": 4,
    }]
    state.native["production_by_planet"] = {
        "0": [{
            "item_id": 0, "item_type": 4, "count": 1,
            "complete_percent": 0, "item_name": "DesignSlot#0",
        }],
    }

    requests = plan_objective_ship_builds(state)
    colony_request = next(request for request in requests if request.role == "colony")
    assert colony_request.quantity == 2
    assert "duplicate active commitments=2" in colony_request.reason

    orders = OrderSet("g", state.year, 1)
    add_economic_orders(state, orders)
    queue = next(
        o.payload["queue"] for o in orders.orders
        if o.kind == "set_planet_queue" and o.payload["planet_id"] == 0
    )
    preserved = next(item for item in queue if item.get("design_slot") == 0)
    assert preserved["quantity"] == 3
    assert preserved["objective_top_up"] == 2
