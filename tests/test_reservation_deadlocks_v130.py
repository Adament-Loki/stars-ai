from stars_ai.models import Fleet, GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.shared_transport import schedule_shared_transport_orders
from stars_ai.strategy.economy import add_economic_orders
from stars_ai.strategy.exploration import add_exploration_orders


def _world(planet_id, name, x, *, owner=None, population=0, observed=True, habitability=70):
    return Planet(
        planet_id, name, Position(x, 0), owner=owner, population=population,
        observed=observed, habitability=habitability,
        native={"is_homeworld": planet_id == 0} if owner == 1 else {},
    )


def _colony_state(planets, fleets):
    return GameState("reservations", 2425, 1, RaceProfile(), Tech(), planets, fleets)


def test_two_launch_ready_colony_ships_take_two_distinct_valid_worlds():
    home=_world(0, "Home", 0, owner=1, population=30_000, habitability=100)
    worlds=[_world(1, "First", 35), _world(2, "Second", 50)]
    fleets=[
        Fleet(3, "Colony One", 1, Position(0, 0), role="colony", native={"position_object_id": 0}),
        Fleet(4, "Colony Two", 1, Position(0, 0), role="colony", native={"position_object_id": 0}),
    ]
    state=_colony_state([home, *worlds], fleets)
    orders=OrderSet("reservations", 2425, 1)

    add_economic_orders(state, orders)

    colony_orders=[order for order in orders.orders if order.kind == "colony_operation"]
    assert len(colony_orders) == 2
    assert {order.payload["destination_planet_id"] for order in colony_orders} == {1, 2}


def test_reserved_best_colony_world_recomputes_to_second_best_valid_world():
    home=_world(0, "Home", 0, owner=1, population=200_000, habitability=100)
    best=_world(1, "Best", 35, habitability=90)
    second=_world(2, "Second", 50, habitability=70)
    fleets=[
        Fleet(3, "Ready", 1, Position(0, 0), role="colony", native={"position_object_id": 0}),
        Fleet(4, "Claimant", 1, Position(35, 0), role="colony", destination_planet_id=1),
    ]
    state=_colony_state([home, best, second], fleets)
    orders=OrderSet("reservations", 2425, 1)
    orders.add(
        "colony_operation", {"fleet_id": 4, "destination_planet_id": 1, "mission": "colonize"},
        "Earlier coordinator reserved Best", priority=130,
    )

    add_economic_orders(state, orders)

    launch=next(
        order for order in orders.orders
        if order.kind == "colony_operation" and order.payload.get("fleet_id") == 3
    )
    assert launch.payload["fleet_id"] == 3
    assert launch.payload["destination_planet_id"] == 2


def test_scout_reservation_recomputes_to_unique_reachable_unknown_world():
    home=_world(0, "Home", 0, owner=1, population=50_000, habitability=100)
    best=_world(1, "Reserved", 30, observed=False)
    second=_world(2, "Second", 40, observed=False)
    scouts=[
        Fleet(3, "Free Scout", 1, Position(0, 0), role="scout"),
        Fleet(4, "Committed Scout", 1, Position(0, 0), role="scout"),
    ]
    state=_colony_state([home, best, second], scouts)
    orders=OrderSet("reservations", 2425, 1)
    orders.add(
        "move_fleet", {"fleet_id": 4, "destination_planet_id": 1, "mission": "scan"},
        "Existing scan claim", priority=120,
    )

    add_exploration_orders(state, orders)

    launch=next(
        order for order in orders.orders
        if order.kind == "move_fleet" and order.payload.get("fleet_id") == 3
    )
    assert launch.payload["destination_planet_id"] == 2


def _capture_freighter(fleet_id, planet_id, x):
    return Fleet(
        fleet_id, f"Capture Transport {fleet_id}", 1, Position(x, 0), role="freighter",
        cargo_capacity=1_000,
        native={
            "position_object_id": planet_id,
            "cargo_capacity": 1_000,
            "cargo": {"ironium": 0, "boranium": 0, "germanium": 0, "population": 0},
        },
    )


def test_capture_reservation_recomputes_to_second_foreign_colony():
    home=_world(0, "Home", 0, owner=1, population=250_000, habitability=100)
    relay=_world(1, "Relay", 120, owner=1, population=500_000, habitability=90)
    relay.native["starbase_capabilities"]={"can_build_ships": True, "can_refuel": True}
    first=_world(9, "First Enemy", 40, owner=2, population=20_000, habitability=80)
    second=_world(10, "Second Enemy", 160, owner=2, population=20_000, habitability=75)
    fleets=[_capture_freighter(3, 0, 0), _capture_freighter(4, 1, 120)]
    state=GameState(
        "capture-reservations", 2415, 1,
        RaceProfile(growth_rate=.15, native={"lrts": ["IFE"]}), Tech(construction=4, propulsion=2),
        [home, relay, first, second], fleets,
    )
    orders=OrderSet("capture-reservations", 2415, 1)

    schedule_shared_transport_orders(state, orders)

    captures=[
        order for order in orders.orders
        if order.kind == "transport_population" and order.payload.get("population_capture")
    ]
    assert len(captures) == 2
    assert {order.payload["destination_planet_id"] for order in captures} == {9, 10}
    assert state.native["shared_transport_schedule"]["reserved_capture_target_ids"] == [9, 10]
