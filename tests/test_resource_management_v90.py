from stars_ai.cargo_planner import derive_cargo_plan
from stars_ai.models import GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.planet_economy import decode_race_economy, estimated_mineral_output, working_mineral_reserve
from stars_ai.research_planner import ResearchDecision
from stars_ai.strategy.economy import _prioritize_economic_infrastructure, add_economic_orders


def _race():
    return RaceProfile(
        native={
            "population_efficiency_raw":10,
            "economy_raw":[10,10,10,10,5,10],
            "flags_73":0,
            "prt_id":9,
        }
    )


def _core(*, minerals=(500,400,400), factories=20, mines=20):
    return Planet(
        0,"Home",Position(0,0),owner=1,observed=True,habitability=100,
        population=100_000,factories=factories,mines=mines,
        ironium=minerals[0],boranium=minerals[1],germanium=minerals[2],
        native={
            "is_homeworld":True,
            "mineral_concentrations":[100,50,25],
        },
    )


def _state(planet):
    return GameState(
        "resource",2405,1,_race(),Tech(),[planet],[],
        native={
            "design_profiles":[],
            "production_by_planet":{"0":[{"item_type":2,"count":1}]},
        },
    )


def _decision(posture):
    return ResearchDecision(
        capability_id="test:technology",
        capability_name="Test Technology",
        category="strategic",
        posture=posture,
        current_field="construction",
        next_field="construction",
        allocation_percent=25,
        score=100,
        estimated_turns=2,
        estimate_confidence="test",
        requirements={"construction":1},
        remaining_requirements={"construction":1},
        contributor_planet_ids=(0,),
        protected_production_planet_ids=(),
        post_unlock_action="test",
        reason="test",
    )


def test_mine_estimate_uses_operable_groups_and_observed_concentration():
    world=_core(mines=29)
    output=estimated_mineral_output(world,decode_race_economy(_race()))
    assert output["operated_mines"]==29
    assert output["mine_groups_of_ten"]==2
    assert output["estimated_mineral_output"] == {
        "ironium":20,"boranium":10,"germanium":5,
    }


def test_core_infrastructure_precedes_optional_work_and_preserves_germanium():
    state=_state(_core())
    orders=OrderSet(state.game_name,state.year,state.player_id)
    add_economic_orders(state,orders)

    queue=next(order.payload["queue"] for order in orders.orders if order.kind=="set_planet_queue")
    assert [item["item"] for item in queue[:2]] == ["factory","mine"]
    assert queue[0]["quantity"]==80
    assert queue[1]["quantity"]==80
    economy=next(order.payload["economy"] for order in orders.orders if order.kind=="set_planet_queue")
    assert economy["economic_core"] is True
    assert economy["mineral_reserve"]["germanium"] >= 320


def test_low_germanium_builds_mines_before_factories():
    world=_core(minerals=(200,200,20),factories=10,mines=4)
    state=_state(world)
    orders=OrderSet(state.game_name,state.year,state.player_id)

    add_economic_orders(state,orders)

    queue=next(order.payload["queue"] for order in orders.orders if order.kind=="set_planet_queue")
    assert queue[0]["item"]=="mine"


def test_mature_mineral_secure_core_builds_factories_before_more_mines():
    world=_core(minerals=(1_181,756,909),factories=10,mines=59)
    state=_state(world)
    orders=OrderSet(state.game_name,state.year,state.player_id)

    add_economic_orders(state,orders)

    queue=next(order.payload["queue"] for order in orders.orders if order.kind=="set_planet_queue")
    assert queue[0]["item"]=="factory"
    assert any(item["item"]=="mine" for item in queue)


def test_saturated_factory_buffer_waits_behind_ships_and_mines():
    ordered=_prioritize_economic_infrastructure([
        {"item":"ship_design","role":"freighter","quantity":1,"strategic_priority":100},
        {"item":"mine","quantity":10,"after_factory_saturation":True},
        {"item":"factory","quantity":5,"overflow_buffer":True},
        {"item":"max_terraform","quantity":1,"after_factory_saturation":True},
    ])

    assert [item["item"] for item in ordered]==[
        "ship_design","mine","factory","max_terraform",
    ]


def test_mines_and_factories_precede_an_unfunded_ship_queue_even_in_a_sprint():
    queue=[
        {"item":"ship_design","design_name":"Opening Scout","quantity":6},
        {"item":"mine","quantity":25},
        {"item":"factory","quantity":25},
        {"item":"starbase_design","quantity":1},
    ]

    ordered=_prioritize_economic_infrastructure(queue)

    assert [item["item"] for item in ordered]==[
        "factory","mine","ship_design","starbase_design",
    ]


def test_opening_queue_places_colonization_ahead_of_preserved_scout_work():
    queue=[
        {"item":"ship_design","design_name":"Probe","role":"scout","quantity":14},
        {"item":"ship_design","design_name":"Mayflower","role":"colony","quantity":2},
        {"item":"mine","quantity":25},
        {"item":"factory","quantity":25},
    ]

    ordered=_prioritize_economic_infrastructure(
        queue,opening_growth=True,colony_launch_priority=True,
    )

    assert [(item["item"],item.get("role")) for item in ordered]==[
        ("ship_design","colony"),("factory",None),("mine",None),("ship_design","scout"),
    ]


def test_deep_ship_backlog_does_not_create_a_full_mineral_reserve_bill():
    reserve=working_mineral_reserve(
        _core(),decode_race_economy(_race()),
        queue=[{"item":"ship_design","quantity":20}],
    )

    assert reserve["queued_ships"]==20
    assert reserve["reserved_ship_horizon"]==3
    assert reserve["ironium"]==300+3*90


def test_only_key_research_sprints_may_displace_shared_economic_growth():
    state=_state(_core())
    normal=OrderSet(state.game_name,state.year,state.player_id)
    add_economic_orders(state,normal,research_decision=_decision("TARGETED"))
    normal_queue=next(order.payload["queue"] for order in normal.orders if order.kind=="set_planet_queue")
    assert any(item["item"]=="mine" for item in normal_queue)
    assert any(item["item"]=="factory" for item in normal_queue)

    sprint=OrderSet(state.game_name,state.year,state.player_id)
    add_economic_orders(state,sprint,research_decision=_decision("SPRINT"))
    sprint_queue=next(order.payload["queue"] for order in sprint.orders if order.kind=="set_planet_queue")
    assert any(item["item"]=="factory" for item in sprint_queue)
    assert any(item["item"]=="mine" for item in sprint_queue)
    execution=state.native["research_sprint_execution"]
    assert execution["requested_research_allocation"] == 25
    assert execution["contributors"] == [{
        "planet_id":0,
        "status":"protected_production",
        "reason":"mines and factories are still below the population cap",
    }]


def test_freight_cannot_drain_a_core_below_its_working_reserve():
    source=_core(minerals=(300,220,220),factories=100,mines=100)
    target=Planet(
        1,"Need",Position(30,0),owner=1,observed=True,population=100_000,
        factories=0,mines=0,ironium=0,boranium=0,germanium=0,
    )
    fleet=type("Freighter",(),{"cargo_capacity":100,"native":{"cargo_capacity":100}})()
    orders=OrderSet("resource",2405,1)
    plan=derive_cargo_plan(source,target,fleet,decode_race_economy(_race()),orders)
    assert plan is None
