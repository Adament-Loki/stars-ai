from stars_ai.models import Fleet, GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.native.x_writer import _fleet_merge_block, _transport_manifest_total, _transport_population_blocks
from stars_ai.shared_transport import _consolidate_bulk_lanes, schedule_shared_transport_orders


def _freighter(fid: int, planet_id: int = 0) -> Fleet:
    return Fleet(
        fid, f"Privateer {fid}", 1, Position(0, 0), role="freighter",
        cargo_capacity=250,
        native={
            "position_object_id": planet_id,
            "cargo_capacity": 250,
            "cargo": {"ironium": 0, "boranium": 0, "germanium": 0, "population": 0},
        },
    )


def _state(fleets=None) -> GameState:
    home = Planet(
        0, "Home", Position(0, 0), owner=1, habitability=100,
        population=100_000, factories=40, mines=30,
        ironium=700, boranium=600, germanium=500,
        native={"is_homeworld": True, "starbase_capabilities": {"can_build_ships": True, "can_refuel": True}},
    )
    p1 = Planet(
        1, "P1", Position(120, 0), owner=1, habitability=80,
        population=50_000, factories=10, mines=10,
        ironium=0, boranium=0, germanium=0,
    )
    unknown = [
        Planet(10 + i, f"U{i}", Position(150 + 7 * i, 20), owner=None, habitability=70)
        for i in range(5)
    ]
    return GameState(
        "shared", 2415, 1, RaceProfile(growth_rate=.15, native={"lrts": ["IFE"]}),
        Tech(construction=4, propulsion=2), [home, p1, *unknown], fleets or [_freighter(3)],
    )


def test_shared_scheduler_fills_p1_population_transport_with_compatible_minerals():
    state = _state()
    orders = OrderSet("shared", 2415, 1)
    # The target's queue makes its I/B/G reserve a real same-destination need.
    orders.add("set_planet_queue", {
        "planet_id": 1,
        "queue": [{"item": "factory", "quantity": 20}, {"item": "defense", "quantity": 10}],
    }, "test")

    schedule_shared_transport_orders(state, orders)

    population = [o for o in orders.orders if o.kind == "transport_population"]
    assert len(population) == 1
    order = population[0]
    assert order.payload["destination_planet_id"] == 1
    assert order.payload["population_kt"] == 80
    assert sum(order.payload["mineral_load"].values()) == 170
    assert order.payload["shared_logistics"]["manifest_total_kt"] == 250
    # One fleet has one single native mission; a second pass cannot claim it.
    assert not [
        o for o in orders.orders
        if o.kind == "transport_minerals" and o.payload.get("fleet_id") == 3
    ]
    assignment = state.native["shared_transport_schedule"]["assignments"][0]
    assert assignment["kind"] == "population"
    assert assignment["manifest_total_kt"] == 250


def test_bulk_population_lane_merges_then_loads_and_transports_in_one_turn():
    state = _state(fleets=[_freighter(3), _freighter(4)])
    orders = OrderSet("shared", 2415, 1)
    orders.add("transport_population", {
        "fleet_id": 3, "source_planet_id": 0, "destination_planet_id": 1,
        "population_kt": 80, "population_colonists": 8_000,
        "mineral_load": {"ironium": 50, "boranium": 50, "germanium": 70},
        "shared_logistics": {"manifest_total_kt": 250},
        "native_experiment": {},
    }, "population shipment", priority=140)
    orders.add("transport_minerals", {
        "fleet_id": 4, "source_planet_id": 0, "destination_planet_id": 1,
        "load": {"ironium": 100, "boranium": 100, "germanium": 50},
    }, "separate mineral shipment", priority=140)
    schedule = [
        {"fleet_id": 3, "kind": "population", "need_rank": 900, "source_planet_id": 0,
         "destination_planet_id": 1, "manifest_total_kt": 250, "capacity_kt": 250},
        {"fleet_id": 4, "kind": "minerals", "need_rank": 900, "source_planet_id": 0,
         "destination_planet_id": 1, "manifest_total_kt": 250, "capacity_kt": 250},
    ]

    _consolidate_bulk_lanes(state, orders, schedule)

    assert not [o for o in orders.orders if o.kind == "transport_minerals"]
    merge = next(o for o in orders.orders if o.kind == "merge_fleets")
    assert merge.payload["target_fleet_id"] == 3
    assert merge.payload["source_fleet_ids"] == [4]
    assert merge.payload["projected_cargo_capacity_kt"] == 500
    assert merge.payload["one_turn_transport"] is True
    transport = next(o for o in orders.orders if o.kind == "transport_population")
    assert transport.payload["fleet_id"] == 3
    assert transport.payload["merged_cargo_capacity_kt"] == 500
    assert transport.payload["population_kt"] == 80
    assert sum(transport.payload["mineral_load"].values()) == 420
    assert schedule[0]["status"] == "combined_after_fleet_merge"
    blocks = _transport_population_blocks(state, transport.payload)
    assert [block.type_id for block in blocks] == [2, 4, 5]
    assert blocks[0].data == bytes.fromhex("03 00 97 00 12 0f 96 00 96 00 78 00 50 00")
    assert blocks[-1].data[-10:] == bytes.fromhex("00 20 00 20 00 20 00 20 00 70")


def test_type37_merge_uses_starsapi_decoded_local_9bit_layout():
    block = _fleet_merge_block({"target_fleet_id": 3, "source_fleet_ids": [4, 511]})
    assert block.type_id == 37
    assert block.data == bytes.fromhex("03 00 04 00 FF 01")


def test_zero_cargo_transport_manifest_is_detected_before_native_encoding():
    assert _transport_manifest_total({
        "population_kt": 0,
        "mineral_load": {"ironium": 0, "boranium": 0, "germanium": 0},
    }) == 0
    assert _transport_manifest_total({
        "population_kt": 80,
        "mineral_load": {"ironium": 20, "boranium": 0, "germanium": 0},
    }) == 100
