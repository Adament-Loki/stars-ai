from stars_ai.models import Fleet, GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.native.x_writer import _fleet_merge_block, _transport_manifest_total, _transport_population_blocks
from stars_ai.native.population_transport import medium_load_block
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


def _large_freighter(fid: int, capacity: int = 1_000) -> Fleet:
    fleet=_freighter(fid)
    fleet.cargo_capacity=capacity
    fleet.native["cargo_capacity"]=capacity
    return fleet


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
    assert order.payload["population_kt"] == 200
    assert sum(order.payload["mineral_load"].values()) == 50
    assert order.payload["shared_logistics"]["manifest_total_kt"] == 250
    # One fleet has one single native mission; a second pass cannot claim it.
    assert not [
        o for o in orders.orders
        if o.kind == "transport_minerals" and o.payload.get("fleet_id") == 3
    ]
    assignment = state.native["shared_transport_schedule"]["assignments"][0]
    assert assignment["kind"] == "population"
    assert assignment["manifest_total_kt"] == 250


def test_large_transport_uses_type2_population_capture_for_small_non_ar_colony():
    state=_state(fleets=[_large_freighter(3)])
    state.planets[0].population=200_000
    target=Planet(
        9,"Enemy Seed",Position(40,0),owner=2,habitability=75,population=20_000,
        observed=True,native={"owner_prt_id":4},
    )
    state.planets.append(target)
    orders=OrderSet("shared",2415,1)

    schedule_shared_transport_orders(state,orders)

    capture=next(
        order for order in orders.orders
        if order.kind=="transport_population"
        and order.payload.get("destination_planet_id")==9
    )
    assert capture.payload["population_kt"]==800
    assert capture.payload["source_population_after"]>=capture.payload["source_protected_floor"]
    assert capture.payload["native_population_encoding"].startswith("type2_")
    blocks=_transport_population_blocks(state,capture.payload)
    assert blocks[0].type_id==2


def test_population_capture_excludes_known_alternate_reality_colony():
    state=_state(fleets=[_large_freighter(3)])
    state.planets[0].population=200_000
    state.planets.append(Planet(
        9,"AR Seed",Position(40,0),owner=2,habitability=75,population=20_000,
        observed=True,native={"owner_prt_id":8,"owner_prt_name":"Alternate Reality"},
    ))
    orders=OrderSet("shared",2415,1)

    schedule_shared_transport_orders(state,orders)

    assert not [
        order for order in orders.orders
        if order.kind=="transport_population"
        and order.payload.get("destination_planet_id")==9
    ]


def test_counter_capture_uses_conservative_lost_world_population_and_marks_the_operation():
    state=_state(fleets=[_large_freighter(3)])
    state.planets[0].population=250_000
    state.planets.append(Planet(
        9,"Lost Relay",Position(40,0),owner=2,habitability=75,population=0,
        observed=True,native={
            "recently_lost_by_player":True,
            "tactical_population_estimate":20_000,
            "tactical_population_estimate_source":"last_friendly_observation",
        },
    ))
    orders=OrderSet("shared",2415,1)

    schedule_shared_transport_orders(state,orders)

    capture=next(
        order for order in orders.orders
        if order.kind=="transport_population"
        and order.payload.get("destination_planet_id")==9
    )
    assert capture.payload["population_kt"]==800
    assert capture.payload["population_capture"]["operation"]=="counter_capture"
    assert "Counter-capture transport" in capture.reason
    # A recapture must be an executable native transport: manual Type-2 load,
    # route to the foreign planet, then the Transport unload task that places
    # the decisive colonists on the world.  Merely annotating a normal hold or
    # a generic move would not contest the captured colony.
    blocks = _transport_population_blocks(state, capture.payload)
    assert [block.type_id for block in blocks] == [2, 4, 5]


def test_counter_capture_never_targets_a_known_alternate_reality_lost_world():
    state=_state(fleets=[_large_freighter(3)])
    state.planets[0].population=250_000
    state.planets.append(Planet(
        9,"Lost AR Relay",Position(40,0),owner=2,habitability=75,population=0,
        observed=True,native={
            "recently_lost_by_player":True,
            "tactical_population_estimate":20_000,
            "owner_prt_id":8,
            "owner_prt_name":"Alternate Reality",
        },
    ))
    orders=OrderSet("shared",2415,1)

    schedule_shared_transport_orders(state,orders)

    assert not [
        order for order in orders.orders
        if order.kind=="transport_population"
        and order.payload.get("destination_planet_id")==9
    ]


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
    assert blocks[0].data == bytes.fromhex("03 00 00 00 12 0f 96 00 96 00 78 00 50 00")
    assert blocks[-1].data[-8:] == bytes.fromhex("00 20 00 20 00 20 00 20")


def test_type37_merge_uses_owner_tagged_wire_fleet_numbers():
    block = _fleet_merge_block(_state(), {"target_fleet_id": 3, "source_fleet_ids": [4, 511]})
    assert block.type_id == 37
    assert block.data == bytes.fromhex("03 00 04 00 FF 01")


def test_player_two_type37_matches_the_merge_then_type2_client_capture():
    state = _state()
    state.player_id = 2

    merge = _fleet_merge_block(state, {"target_fleet_id": 5, "source_fleet_ids": [6]})
    load = medium_load_block(
        state, 5, {"germanium": 250, "population": 840},
        source_planet_id=19, capacity_override=1_090,
    )

    # User-authored latestdemo turn-22 P2 transaction: every fleet reference
    # carries the same P2 owner tag (0x0200), avoiding the former Type37/P2
    # mismatch that left only the merge referring to P1 fleet numbers.
    assert merge.data == bytes.fromhex("05 02 06 02")
    assert load.data == bytes.fromhex("05 02 13 00 12 0c fa 00 48 03")


def test_zero_cargo_transport_manifest_is_detected_before_native_encoding():
    assert _transport_manifest_total({
        "population_kt": 0,
        "mineral_load": {"ironium": 0, "boranium": 0, "germanium": 0},
    }) == 0
    assert _transport_manifest_total({
        "population_kt": 80,
        "mineral_load": {"ironium": 20, "boranium": 0, "germanium": 0},
    }) == 100


def test_stranded_cargo_receives_a_current_orbit_unload_all_recovery_order():
    freighter = _freighter(3)
    freighter.native["cargo"]["population"] = 80
    state = _state(fleets=[freighter])
    orders = OrderSet("shared", 2415, 1)

    schedule_shared_transport_orders(state, orders)

    recovery=[order for order in orders.orders if order.kind == "transport_unload_remainder"]
    assert len(recovery)==1
    assert recovery[0].payload["fleet_id"]==3
    assert recovery[0].payload["destination_planet_id"]==0
    assert recovery[0].payload["unload"] == {
        "ironium":"all","boranium":"all","germanium":"all","population":"all",
    }
    assert not [
        order for order in orders.orders
        if order.kind in {"transport_population", "transport_minerals"}
    ]
    assert state.native["shared_transport_schedule"]["assignments"][0]["status"] == "current_orbit_unload_all"
    assert state.native["shared_transport_schedule"]["native_delivery_recovery_blocked_fleet_ids"] == []


def test_stranded_freighter_recovers_without_stopping_empty_transport_work():
    stranded = _freighter(3)
    stranded.native["cargo"]["population"] = 80
    available = _freighter(4)
    state = _state(fleets=[stranded, available])
    orders = OrderSet("shared", 2415, 1)

    schedule_shared_transport_orders(state, orders)

    shipments = [order for order in orders.orders if order.kind == "transport_population"]
    assert len(shipments) == 1
    assert shipments[0].payload["fleet_id"] == 4
    recovery=[order for order in orders.orders if order.kind == "transport_unload_remainder"]
    assert [order.payload["fleet_id"] for order in recovery] == [3]
    assert state.native["shared_transport_schedule"]["native_delivery_recovery_blocked_fleet_ids"] == []


def test_source_surplus_allows_every_useful_small_transport_to_depart():
    state = _state(fleets=[_freighter(3), _freighter(4), _freighter(5)])
    state.planets[0].population = 650_000
    orders = OrderSet("shared", 2415, 1)

    schedule_shared_transport_orders(state, orders)

    shipments = [order for order in orders.orders if order.kind == "transport_population"]
    assert len(shipments) == 3
    assert {order.payload["fleet_id"] for order in shipments} == {3, 4, 5}
    assert all(order.payload["population_kt"] == 250 for order in shipments)
    assert [order.payload["source_population_before"] for order in shipments] == [650_000, 625_000, 600_000]


def test_five_small_transports_can_depart_when_source_surplus_and_destination_need_allow():
    state = _state(fleets=[_freighter(fid) for fid in range(3, 8)])
    state.planets[0].population = 650_000
    orders = OrderSet("shared", 2415, 1)

    schedule_shared_transport_orders(state, orders)

    shipments = [order for order in orders.orders if order.kind == "transport_population"]
    assert len(shipments) == 5
    assert all(order.payload["population_kt"] == 250 for order in shipments)
    assert [order.payload["source_population_before"] for order in shipments] == [
        650_000, 625_000, 600_000, 575_000, 550_000,
    ]
    assert all(
        order.payload["source_population_after"] >= order.payload["source_protected_floor"]
        for order in shipments
    )


def test_large_freighters_are_limited_by_need_and_hold_not_a_100k_packet_cap():
    state = _state(fleets=[_large_freighter(3, 1_500), _large_freighter(4, 1_500)])
    state.planets[0].population = 650_000
    state.planets.append(Planet(
        2, "P2", Position(0, 120), owner=1, habitability=80,
        population=50_000, factories=10, mines=10,
        ironium=0, boranium=0, germanium=0,
    ))
    orders = OrderSet("shared", 2415, 1)

    schedule_shared_transport_orders(state, orders)

    shipments = [order for order in orders.orders if order.kind == "transport_population"]
    assert len(shipments) == 2
    assert {order.payload["fleet_id"] for order in shipments} == {3, 4}
    assert all(order.payload["population_kt"] == 1_500 for order in shipments)
    assert {order.payload["destination_planet_id"] for order in shipments} == {1, 2}
    assert [order.payload["source_population_before"] for order in shipments] == [650_000, 500_000]
    assert all(
        _transport_population_blocks(state, order.payload)[0].type_id == 2
        for order in shipments
    )


def test_idle_freighter_at_nonexporter_returns_to_ready_homeworld_before_new_builds():
    freighter = _freighter(3)
    freighter.position = Position(120, 0)
    freighter.native["position_object_id"] = 1
    state = _state(fleets=[freighter])
    orders = OrderSet("shared", 2415, 1)

    schedule_shared_transport_orders(state, orders)

    return_order = next(order for order in orders.orders if order.kind == "move_fleet")
    assert return_order.payload["fleet_id"] == 3
    assert return_order.payload["destination_planet_id"] == 0
    assert return_order.payload["mission"] == "return_for_population_export"
