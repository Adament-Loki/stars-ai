from stars_ai.fleet_management import enforce_scout_split_precedence, prepare_scout_split_precedence
from stars_ai.models import Fleet, GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.strategy.exploration import add_exploration_orders


def _sparse_slot_stack() -> GameState:
    home=Planet(0,"Home",Position(0,0),owner=1,observed=True,native={"is_homeworld":True})
    unknown=Planet(1,"Unknown",Position(35,0),observed=False)
    stack=Fleet(
        7,"Six Scouts",1,Position(0,0),role="scout",
        native={"ship_count":[0,0,0,0,0,6]+[0]*10,"position_object_id":0},
    )
    # The decoded native table can be sparse; list index zero is not slot five.
    return GameState(
        "split-precedence",2425,1,RaceProfile(),Tech(),[home,unknown],[stack],
        native={"design_profiles":[{"design_number":5,"role":"scout"}]},
    )


def test_six_scout_stack_unconditionally_preempts_existing_routing_and_cancel():
    state=_sparse_slot_stack()
    orders=OrderSet("split-precedence",2425,1)
    orders.add("move_fleet",{"fleet_id":7,"destination_planet_id":1,"mission":"scan"},"ordinary recon",priority=250)
    orders.add("cancel_scout_route",{"fleet_id":7,"reason":"ordinary repair"},"ordinary cancellation",priority=251)

    decisions=enforce_scout_split_precedence(state,orders)

    split_orders=[order for order in orders.orders if order.kind=="split_scout_fleet"]
    assert len(split_orders)==1
    assert split_orders[0].payload["scout_slot"]==5
    assert split_orders[0].payload["singleton_fleets_required"]==5
    assert not [
        order for order in orders.orders
        if order.kind in {"move_fleet","cancel_scout_route"}
        and order.payload.get("fleet_id")==7
    ]
    assert decisions[0]["outcome"]=="split_wins"
    assert decisions[0]["suppressed_order_kinds"]==["cancel_scout_route","move_fleet"]
    assert state.native["pending_scout_split_fleet_ids"]==[7]


def test_split_preflight_defers_routing_until_next_host_readback():
    state=_sparse_slot_stack()
    orders=OrderSet("split-precedence",2425,1)

    prepare_scout_split_precedence(state,orders)
    add_exploration_orders(state,orders)

    assert state.native["scout_exploration_policy"]["split_pending_fleet_ids"]==[7]
    assert not [
        order for order in orders.orders
        if order.kind in {"move_fleet","cancel_scout_route"}
        and order.payload.get("fleet_id")==7
    ]


def test_genuine_recovery_retains_native_caution_and_clears_pending_split():
    state=_sparse_slot_stack()
    orders=OrderSet("split-precedence",2425,1)
    prepare_scout_split_precedence(state,orders)
    orders.add(
        "move_fleet",
        {"fleet_id":7,"destination_planet_id":0,"mission":"fleet_recovery","emergency":True},
        "fuel emergency",priority=300,
    )

    decisions=enforce_scout_split_precedence(state,orders)

    assert not [order for order in orders.orders if order.kind=="split_scout_fleet"]
    assert [order for order in orders.orders if order.kind=="move_fleet"]
    assert decisions[0]["outcome"]=="recovery_wins"
    assert state.native["pending_scout_split_fleet_ids"]==[]


def test_host_readback_exposes_six_singleton_routing_candidates():
    home=Planet(0,"Home",Position(0,0),owner=1,observed=True,native={"is_homeworld":True})
    unknown=[Planet(i,f"U{i}",Position(30+i*3,0),observed=False) for i in range(1,7)]
    scouts=[Fleet(i,f"Scout {i}",1,Position(0,0),role="scout") for i in range(10,16)]
    state=GameState("readback",2426,1,RaceProfile(),Tech(),[home,*unknown],scouts,
                    native={"design_profiles":[{"design_number":5,"role":"scout"}]})
    orders=OrderSet("readback",2426,1)

    add_exploration_orders(state,orders)

    policy=state.native["scout_exploration_policy"]
    scans=[order for order in orders.orders if order.kind=="move_fleet" and order.payload.get("mission")=="scan"]
    assert policy["mission_capable_singleton_scout_ids"]==[10,11,12,13,14,15]
    assert policy["singleton_scout_capacity"]==6
    assert len({order.payload["destination_planet_id"] for order in scans})==6
