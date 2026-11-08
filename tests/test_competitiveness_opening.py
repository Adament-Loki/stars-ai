from stars_ai.models import Fleet, GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.starbase_planner import plan_support_base_builds
from stars_ai.persona import BalancedPersona
from stars_ai.strategy.economy import add_economic_orders
from stars_ai.strategy.military import add_military_orders
from tests.test_shared_transport_v103 import _large_freighter, _state


def test_frontier_contact_keeps_expansion_ahead_of_a_nearby_fleet():
    planets = [
        Planet(index, f"Colony {index}", Position(index * 70, 400), owner=1, observed=True, habitability=80)
        for index in range(8)
    ]
    planets.append(Planet(20, "Border", Position(0, 0), owner=1, observed=True, habitability=70))
    planets += [
        Planet(30 + index, f"Unknown {index}", Position(600 + index * 40, 90), observed=False)
        for index in range(5)
    ]
    planets.append(Planet(9, "Theirs", Position(40, 0), owner=2, observed=True, habitability=70))
    state = GameState(
        "frontier", 2420, 1, RaceProfile(), Tech(), planets,
        [Fleet(1, "Nearby", 2, Position(30, 0), role="combat", combat_power=40)],
    )

    plan = BalancedPersona().build_plan(state)

    assert plan.phase == "frontier_contact"
    assert plan.planet_actions[20] == "hold_line"
    assert plan.objective("expand") >= plan.objective("defend") * 1.2


def test_resource_floor_world_colonizes_before_the_idle_fallback():
    home = Planet(0, "Home", Position(0, 0), owner=1, observed=True, habitability=100, population=200_000)
    claim = Planet(1, "Claim", Position(40, 0), observed=True, habitability=55)
    colony = Fleet(
        3, "Colony", 1, Position(0, 0), role="colony", speed=7,
        native={"position_object_id": 0, "idle_colony_turns": 0},
    )
    state = GameState("floor", 2410, 1, RaceProfile(), Tech(), [home, claim], [colony])
    orders = OrderSet("floor", 2410, 1)

    add_economic_orders(state, orders)

    colonize = next(order for order in orders.orders if order.kind == "colony_operation")
    assert colonize.payload["destination_planet_id"] == 1
    diagnostic = next(row for row in state.native["colony_target_diagnostics"] if row["planet_id"] == 1)
    assert diagnostic["fallback_used"] is False


def test_neutral_undefended_colony_is_a_population_capture():
    state = _state(fleets=[_large_freighter(3)])
    state.planets[0].population = 200_000
    state.planets.append(Planet(
        9, "Enemy Seed", Position(40, 0), owner=2, habitability=75, population=20_000,
        observed=True, native={"owner_prt_id": 4},
    ))
    plan = BalancedPersona().build_plan(state)
    orders = OrderSet("shared", 2415, 1)

    from stars_ai.shared_transport import schedule_shared_transport_orders
    schedule_shared_transport_orders(state, orders, plan)

    assert plan.diplomacy[2]["attitude"] != "allied"
    capture = next(
        order for order in orders.orders
        if order.kind == "transport_population" and order.payload.get("destination_planet_id") == 9
    )
    assert capture.payload["population_capture"]


def test_allied_border_starbase_uses_the_cheaper_unarmed_design():
    support = {"can_build_ships": True, "can_refuel": True, "name": "Space Station"}
    home = Planet(
        0, "Home", Position(0, 0), owner=1, habitability=100, population=350_000,
        factories=80, mines=50, ironium=180, boranium=120, germanium=120,
        native={"is_homeworld": True, "has_starbase": True, "starbase_capabilities": support},
    )
    colonies = [
        Planet(
            index, f"P{index}", Position(index * 70, 0), owner=1, habitability=80,
            population=140_000, factories=40, mines=20,
            ironium=80, boranium=80, germanium=80,
        )
        for index in range(1, 8)
    ]
    ally = Planet(20, "Ally", Position(-40, 0), owner=2, observed=True, habitability=80, population=80_000)
    state = GameState(
        "cheap-base", 2425, 1, RaceProfile(), Tech(construction=8),
        [home, *colonies, ally], [],
        native={
            "configured_allies": [2],
            "production_by_planet": {},
            "design_profiles": [],
            "starbase_profiles": [
                {
                    "design_number": 0, "name": "Armed Station", "hull_id": 34,
                    "hull_name": "Space Station", "capabilities": support,
                    "slots": [{"category": 16, "item_id": 1, "count": 8}],
                },
                {
                    "design_number": 1, "name": "Plain Station", "hull_id": 34,
                    "hull_name": "Space Station", "capabilities": support,
                    "slots": [],
                },
            ],
        },
    )

    builds = plan_support_base_builds(state)

    assert builds
    assert all(build.design_slot == 1 for build in builds)
    assert all("cheap unarmed support base" in build.reason for build in builds)


def test_configured_ally_colony_is_not_attacked_or_captured():
    state = _state(fleets=[
        _large_freighter(3),
        Fleet(4, "Patrol", 1, Position(0, 0), role="combat", combat_power=80, speed=8),
    ])
    state.planets[0].population = 200_000
    state.planets.append(Planet(
        9, "Ally World", Position(40, 0), owner=2, habitability=75, population=20_000,
        observed=True,
    ))
    state.native["configured_allies"] = [2]
    plan = BalancedPersona().build_plan(state)
    orders = OrderSet("shared", 2415, 1)

    from stars_ai.shared_transport import schedule_shared_transport_orders
    schedule_shared_transport_orders(state, orders, plan)
    add_military_orders(state, orders, plan)

    assert plan.diplomacy[2]["attitude"] == "allied"
    assert not any(
        order.kind == "transport_population" and order.payload.get("destination_planet_id") == 9
        for order in orders.orders
    )
    assert not any(
        order.kind == "move_fleet" and order.payload.get("destination_planet_id") == 9
        for order in orders.orders
    )
