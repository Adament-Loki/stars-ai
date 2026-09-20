import pytest

from stars_ai.fleet_management import add_tactical_fleet_management, enforce_scout_split_precedence
from stars_ai.fleet_intent import ensure_fleet_activity
from stars_ai.memory import AgentMemory
from stars_ai.models import Fleet, GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.objective_production import _pick, _selected_role_gap, plan_objective_ship_builds
from stars_ai.shared_transport import schedule_shared_transport_orders
from stars_ai.research_capabilities import ResearchCapability
from stars_ai.research_planner import (
    ResearchDecision,
    ResearchDemand,
    _competitive_tech_pressure,
    plan_research,
)
from stars_ai.strategy.economy import (
    _mineral_emergency_snapshot,
    _prioritize_economic_infrastructure,
    _resource_runway,
    add_economic_orders,
)
from stars_ai.strategy.exploration import add_exploration_orders
from stars_ai.strategy.research import add_research_orders


def _stack_state(count: int) -> GameState:
    home=Planet(0,"Home",Position(0,0),owner=1,observed=True)
    fleet=Fleet(
        7,"Scout Stack",1,Position(0,0),role="scout",
        native={"ship_count":[count]+[0]*15,"position_object_id":0},
    )
    return GameState(
        "split",2425,1,RaceProfile(),Tech(),[home],[fleet],
        native={"design_profiles":[{"design_number":0,"role":"scout"}]},
    )


@pytest.mark.parametrize("count",[2,3,6])
def test_pure_scout_split_preempts_routine_movement(count):
    state=_stack_state(count)
    orders=OrderSet("split",2425,1)
    orders.add("move_fleet",{"fleet_id":7,"destination_planet_id":0,"mission":"scan"},"old scout route",priority=220)

    add_tactical_fleet_management(state,orders)
    decisions=enforce_scout_split_precedence(state,orders)

    split=next(order for order in orders.orders if order.kind=="split_scout_fleet")
    assert split.payload["singleton_fleets_required"]==count-1
    assert not [order for order in orders.orders if order.kind=="move_fleet" and order.payload.get("fleet_id")==7]
    assert decisions==[{"fleet_id":7,"outcome":"split_wins","suppressed_order_kinds":["move_fleet"],"reason":"Safe pure-scout stack is split before any routine route; singleton routing waits for next M-file read-back."}]
    assert state.native["scout_stack_diagnostics"]["wasted_survey_capacity"]==count-1


def test_readback_singleton_scouts_receive_unique_local_routes():
    home=Planet(0,"Home",Position(0,0),owner=1,observed=True,native={"is_homeworld":True})
    unknown=[Planet(i,f"U{i}",Position(35+i*4,0),observed=False) for i in range(1,13)]
    fleets=[Fleet(i,f"Scout {i}",1,Position(0,0),role="scout") for i in range(6)]
    state=GameState("readback",2426,1,RaceProfile(),Tech(),[home,*unknown],fleets,
                    native={"design_profiles":[{"design_number":0,"role":"scout"}]})
    orders=OrderSet("readback",2426,1)

    add_exploration_orders(state,orders)

    scans=[order for order in orders.orders if order.kind=="move_fleet" and order.payload.get("mission")=="scan"]
    assert len(scans)==6
    assert len({order.payload["destination_planet_id"] for order in scans})==6
    assert state.native["scout_exploration_policy"]["singleton_scout_capacity"]==6


def test_scout_gap_uses_total_force_not_scout_generation_throttle():
    designs={
        0:{"design_number":0,"role":"scout","turn_designed":2},
        1:{"design_number":1,"role":"scout","turn_designed":10},
    }
    gap,reason=_selected_role_gap(designs,{0:2,1:0},"scout",designs[1],desired=24,role_assets=2)

    assert gap==22
    assert "desired total=24" in reason


def test_urgent_ship_horizon_precedes_factory_batch_but_routine_does_not():
    urgent=[
        {"item":"ship_design","role":"scout","design_name":"Surveyor","quantity":6,"strategic_priority":196},
        {"item":"factory","quantity":25},
    ]
    normal=[
        {"item":"ship_design","role":"scout","design_name":"Surveyor","quantity":6,"strategic_priority":160},
        {"item":"factory","quantity":25},
    ]

    urgent_queue=_prioritize_economic_infrastructure(urgent)
    normal_queue=_prioritize_economic_infrastructure(normal)

    assert urgent_queue[0]["item"]=="ship_design"
    assert urgent_queue[0]["quantity"]==2
    assert urgent_queue[0]["queue_priority_band"]==4
    assert urgent_queue[1]["item"]=="factory"
    assert normal_queue[0]["item"]=="factory"


def test_emergency_combat_precedes_routine_factory_batch():
    queue=[
        {"item":"ship_design","role":"combat","design_name":"Escort","quantity":2,
         "strategic_priority":170,"strategic_reason":"territorial response required"},
        {"item":"factory","quantity":25},
    ]

    ordered=_prioritize_economic_infrastructure(queue)

    assert ordered[0]["role"]=="combat"
    assert ordered[0]["queue_priority_band"]==1


def test_local_unknown_wins_over_distant_frontier_target():
    home=Planet(0,"Home",Position(0,0),owner=1,observed=True,native={"is_homeworld":True})
    near=Planet(1,"Near",Position(50,0),observed=False)
    far=Planet(2,"Far",Position(250,0),observed=False)
    scout=Fleet(0,"Scout",1,Position(0,0),role="scout")
    state=GameState("local",2425,1,RaceProfile(),Tech(),[home,near,far],[scout])
    orders=OrderSet("local",2425,1)

    add_exploration_orders(state,orders)

    scan=next(order for order in orders.orders if order.kind=="move_fleet")
    assert scan.payload["destination_planet_id"]==1
    assert state.native["scout_exploration_policy"]["unknown_within_home_radius"]==1


def test_survey_selection_ignores_weapon_generation_but_armed_recon_can_use_it(monkeypatch):
    state=GameState("design",2425,1,RaceProfile(),Tech(),[],[],native={"design_profiles":[
        {"design_number":0,"role":"scout","name":"Long Survey","test_range":500,"scanner_range":50,
         "dry_mass":25,"fuel_capacity":200,"turn_designed":1},
        {"design_number":1,"role":"scout","name":"Armed Short","test_range":120,"scanner_range":10,
         "weapon_count":4,"dry_mass":25,"fuel_capacity":80,"turn_designed":20},
    ]})
    monkeypatch.setattr("stars_ai.objective_production.best_range_ly",lambda design,*_:design["test_range"])

    assert _pick(state,"scout")["name"]=="Long Survey"
    assert _pick(state,"scout",mission="contested_border")["name"]=="Armed Short"


def test_severe_zero_ironium_recovery_precedes_an_ordinary_scout():
    emergency=_mineral_emergency_snapshot(
        {"ironium":0,"boranium":800,"germanium":800},
        {"ironium":570,"boranium":100,"germanium":100},
        {"estimated_mineral_output":{"ironium":5,"boranium":30,"germanium":30},"operated_mines":10},
        mine_headroom=90,
    )
    queue=[
        {"item":"ship_design","role":"scout","quantity":3,"strategic_priority":196},
        {"item":"mine","quantity":25,"mineral_deficits":{"ironium":570},"mineral_emergency":emergency},
    ]

    ordered=_prioritize_economic_infrastructure(queue,mineral_emergency=emergency)

    assert emergency["active"] is True
    assert 5 <= emergency["mines_required_for_recovery"] <= 10
    assert ordered[0]["item"]=="mine"
    assert ordered[0]["queue_priority_band"]==2


def test_population_throughput_shortfall_builds_compact_relief_and_reserves_the_idle_hold():
    home=Planet(
        0,"Home",Position(0,0),owner=1,observed=True,habitability=100,
        population=1_100_000,ironium=2_000,boranium=2_000,germanium=2_000,
        native={"is_homeworld":True,"starbase_capabilities":{"can_build_ships":True,"can_refuel":True}},
    )
    children=[
        Planet(index,f"P1-{index}",Position(60+index*10,0),owner=1,observed=True,
               habitability=80,population=0)
        for index in range(1,5)
    ]
    freighter=Fleet(
        7,"Privateer",1,Position(0,0),role="freighter",cargo_capacity=250,
        native={
            "position_object_id":0,"cargo_capacity":250,"ship_count":[1],
            "cargo":{"ironium":0,"boranium":0,"germanium":0,"population":0},
        },
    )
    state=GameState(
        "throughput",2425,1,RaceProfile(),Tech(),[home,*children],[freighter],
        native={"design_profiles":[
            {"design_number":0,"name":"Privateer","role":"freighter","cargo_capacity":250,
             "dry_mass":25,"fuel_capacity":250,"engine_id":4},
        ],"production_by_planet":{"0":[]}},
    )

    requests=plan_objective_ship_builds(state)
    throughput=state.native["population_transport_throughput"]
    relief=next(request for request in requests if request.role=="freighter")

    assert throughput["backlog_colonists"] >= 900_000
    assert throughput["exportable_population"] > 900_000
    assert throughput["desired_transport_kt"] >= 1_000
    assert throughput["transport_shortfall"] >= 750
    assert relief.quantity >= 3
    assert relief.design_name=="Privateer"

    orders=OrderSet("throughput",2425,1)
    schedule_shared_transport_orders(state,orders)
    assignment=next(row for row in state.native["shared_transport_schedule"]["assignments"] if row["fleet_id"]==7)
    final_throughput=state.native["population_transport_throughput"]
    assert assignment["kind"]=="population"
    assert final_throughput["active_population_transport_kt"]==250
    assert final_throughput["reserved_from_mineral_work"] is True


def test_zero_ironium_runway_recovers_before_an_urgent_scout_and_is_auditable():
    home=Planet(
        0,"Home",Position(0,0),owner=1,observed=True,habitability=100,
        population=200_000,factories=30,mines=10,ironium=0,boranium=800,germanium=800,
        native={
            "is_homeworld":True,"mineral_concentrations":[50,50,50],
            "starbase_capabilities":{"can_build_ships":True,"can_refuel":True},
        },
    )
    unknown=Planet(1,"Unknown",Position(90,0),owner=None,observed=False,habitability=75)
    state=GameState(
        "zero-iron",2425,1,RaceProfile(),Tech(),[home,unknown],[],
        native={
            "design_profiles":[{"design_number":0,"name":"Scout","role":"scout","dry_mass":25,
                                 "fuel_capacity":250,"engine_id":4}],
            "production_by_planet":{"0":[]},
            "strategic_watchdog":{"exploration_pressure":1.8},
        },
    )
    orders=OrderSet("zero-iron",2425,1)

    add_economic_orders(state,orders)

    queue=next(order.payload["queue"] for order in orders.orders if order.kind=="set_planet_queue")
    mine_index=next(index for index,item in enumerate(queue) if item["item"]=="mine")
    scout_index=next(index for index,item in enumerate(queue) if item.get("role")=="scout")
    runway=state.native["resource_runway"]["0"]
    assert mine_index < scout_index
    assert queue[mine_index]["quantity"] <= 10
    assert queue[mine_index]["queue_priority_band"]==2
    assert runway["stock_by_mineral"]["ironium"]==0
    assert runway["working_reserve"]["ironium"] > 0
    assert runway["turns_to_material_block"]["ironium"]==0
    assert set(runway) >= {
        "stock_by_mineral","working_reserve","projected_income",
        "committed_queue_cost","turns_to_material_block",
    }


def test_healthy_runway_puts_a_scout_emergency_ahead_of_routine_factories():
    runway={"immediately_executable":True,"turns_to_material_block":{"ironium":None}}
    queue=[
        {"item":"factory","quantity":25},
        {"item":"ship_design","role":"scout","quantity":3,"strategic_priority":196},
    ]

    ordered=_prioritize_economic_infrastructure(queue,resource_runway=runway)

    assert ordered[0]["role"]=="scout"
    assert ordered[0]["queue_priority_band"]==4
    assert ordered[1]["item"]=="factory"


def test_active_invasion_puts_combat_ahead_of_runway_and_routine_growth():
    queue=[
        {"item":"mine","quantity":6,"mineral_emergency":{"active":True}},
        {"item":"factory","quantity":25},
        {"item":"ship_design","role":"combat","quantity":2,"strategic_priority":170,
         "strategic_reason":"territorial invasion response"},
    ]

    ordered=_prioritize_economic_infrastructure(
        queue,mineral_emergency={"active":True},
        resource_runway={"immediately_executable":False},invasion_active=True,
    )

    assert ordered[0]["role"]=="combat"
    assert ordered[0]["queue_priority_band"]==1
    assert ordered[1]["item"]=="mine"
    assert ordered[1]["queue_priority_band"]==2


def test_single_local_unknown_cancels_only_one_outer_scout_route():
    home=Planet(0,"Home",Position(0,0),owner=1,observed=True,native={"is_homeworld":True})
    local=Planet(1,"Local",Position(50,0),observed=False)
    outer=Planet(2,"Outer",Position(250,0),observed=False)
    scouts=[
        Fleet(i,f"Scout {i}",1,Position(0,0),destination_planet_id=2,role="scout")
        for i in range(15)
    ]
    state=GameState("cancel-one",2425,1,RaceProfile(),Tech(),[home,local,outer],scouts)
    orders=OrderSet("cancel-one",2425,1)

    add_exploration_orders(state,orders)

    cancellations=[order for order in orders.orders if order.kind=="cancel_scout_route"]
    assert len(cancellations)==1
    assert state.native["scout_exploration_policy"]["required_local_scouts"]==1


def test_local_global_assignment_spreads_three_scouts_across_three_local_worlds():
    home=Planet(0,"Home",Position(0,0),owner=1,observed=True,native={"is_homeworld":True})
    local=[Planet(i,f"L{i}",Position(35+i*10,0),observed=False) for i in range(1,4)]
    frontier=[Planet(20+i,f"F{i}",Position(180+i*10,0),observed=False) for i in range(10)]
    scouts=[Fleet(i,f"Scout {i}",1,Position(0,0),role="scout") for i in range(5)]
    state=GameState("global",2425,1,RaceProfile(),Tech(),[home,*local,*frontier],scouts)
    orders=OrderSet("global",2425,1)

    add_exploration_orders(state,orders)

    scans=[order for order in orders.orders if order.kind=="move_fleet" and order.payload.get("mission")=="scan"]
    assert len({order.payload["destination_planet_id"] for order in scans})==5
    assert {order.payload["destination_planet_id"] for order in scans[:3]}=={1,2,3}
    assert all(row["status"]!="BLOCKED" for row in state.native["scout_exploration_policy"]["final_audit"])


def test_scout_final_audit_makes_a_route_or_explains_the_block():
    home=Planet(0,"Home",Position(0,0),owner=1,observed=True,native={"is_homeworld":True})
    unknown=Planet(1,"Unknown",Position(40,0),observed=False)
    scout=Fleet(8,"Scout",1,Position(0,0),role="scout")
    state=GameState("audit",2425,1,RaceProfile(),Tech(),[home,unknown],[scout])
    orders=OrderSet("audit",2425,1)

    add_exploration_orders(state,orders)

    audit=state.native["scout_exploration_policy"]["final_audit"]
    assert audit==[{"fleet_id":8,"status":"active_route","blocked_reason":None}]


def test_global_scout_reservation_assigns_twenty_distinct_targets():
    home=Planet(0,"Home",Position(0,0),owner=1,observed=True,native={"is_homeworld":True})
    unknown=[
        Planet(i,f"U{i}",Position(140+(i%20)*8,140+(i//20)*8),observed=False)
        for i in range(1,101)
    ]
    scouts=[Fleet(i,f"Scout {i}",1,Position(0,0),role="scout") for i in range(20)]
    state=GameState("twenty-scouts",2425,1,RaceProfile(),Tech(),[home,*unknown],scouts)
    orders=OrderSet("twenty-scouts",2425,1)

    add_exploration_orders(state,orders)

    scans=[order for order in orders.orders if order.kind=="move_fleet" and order.payload.get("mission")=="scan"]
    assert len(scans)==20
    assert len({order.payload["destination_planet_id"] for order in scans})==20
    assert state.native["scout_exploration_policy"]["duplicate_scout_targets"]==0


def test_finished_scout_route_receives_a_new_unique_target_next_cycle():
    home=Planet(0,"Home",Position(0,0),owner=1,observed=True,native={"is_homeworld":True})
    first=Planet(1,"First",Position(45,0),observed=False)
    second=Planet(2,"Second",Position(70,0),observed=False)
    scout=Fleet(7,"Scout",1,Position(0,0),role="scout")
    state=GameState("scout-cycle",2425,1,RaceProfile(),Tech(),[home,first,second],[scout])
    first_orders=OrderSet("scout-cycle",2425,1)

    add_exploration_orders(state,first_orders)
    first_target=next(order for order in first_orders.orders if order.kind=="move_fleet")
    assert first_target.payload["destination_planet_id"]==1

    # Host read-back: the route's first target was observed and the scout is
    # idle again.  Planning must issue the remaining target immediately.
    first.observed=True
    scout.destination_planet_id=None
    state.year=2426
    second_orders=OrderSet("scout-cycle",2426,1)
    add_exploration_orders(state,second_orders)

    next_target=next(order for order in second_orders.orders if order.kind=="move_fleet")
    assert next_target.payload["destination_planet_id"]==2
    assert state.native["scout_exploration_policy"]["final_audit"]==[
        {"fleet_id":7,"status":"active_route","blocked_reason":None}
    ]


def test_idle_singleton_scout_is_explicitly_blocked_when_no_unknowns_remain():
    home=Planet(0,"Home",Position(0,0),owner=1,observed=True,native={"is_homeworld":True})
    scout=Fleet(3,"Scout",1,Position(0,0),role="scout")
    state=GameState("empty-sky",2425,1,RaceProfile(),Tech(),[home],[scout])
    orders=OrderSet("empty-sky",2425,1)

    add_exploration_orders(state,orders)

    audit=state.native["scout_exploration_policy"]["final_audit"]
    assert audit==[{
        "fleet_id":3,"status":"BLOCKED",
        "blocked_reason":"no unobserved worlds remain for reconnaissance",
    }]


def test_live_uncommitted_claim_reserves_a_colony_hull_ahead_of_routine_growth():
    home=Planet(
        0,"Home",Position(0,0),owner=1,observed=True,habitability=100,
        population=100_000,factories=10,mines=10,
        ironium=2_000,boranium=2_000,germanium=2_000,
        native={"is_homeworld":True,"starbase_capabilities":{"can_build_ships":True,"can_refuel":True}},
    )
    claim=Planet(1,"Claim",Position(45,0),observed=True,habitability=75)
    state=GameState(
        "colony-pipeline",2425,1,RaceProfile(),Tech(),[home,claim],[],
        native={
            "design_profiles":[
                {"design_number":2,"name":"Mayflower","role":"colony","dry_mass":65,"fuel_capacity":200,"engine_id":4},
            ],
            "production_by_planet":{"0":[]},
        },
    )
    orders=OrderSet("colony-pipeline",2425,1)

    requests=plan_objective_ship_builds(state)
    colony=next(request for request in requests if request.role=="colony")
    assert colony.quantity>=1
    pipeline=state.native["colony_pipeline"]
    assert pipeline["desired_colony_pipeline"]==1
    assert pipeline["required_new_hulls"]>=1
    assert pipeline["build_authorized"] is True

    add_economic_orders(state,orders)

    queue=next(order.payload["queue"] for order in orders.orders if order.kind=="set_planet_queue")
    colony_index=next(index for index,item in enumerate(queue) if item.get("role")=="colony")
    routine_growth_indexes=[
        index for index,item in enumerate(queue)
        if item.get("item") in {"factory","mine"}
        and item.get("queue_priority_band")==6
    ]
    assert colony_index==0
    assert all(colony_index<index for index in routine_growth_indexes)


def test_no_valid_claim_does_not_create_a_speculative_colony_queue():
    home=Planet(
        0,"Home",Position(0,0),owner=1,observed=True,habitability=100,
        population=100_000,ironium=2_000,boranium=2_000,germanium=2_000,
        native={"is_homeworld":True,"starbase_capabilities":{"can_build_ships":True,"can_refuel":True}},
    )
    # An unobserved target is not a phase-eligible claim and must not prompt a
    # speculative colony backlog.
    unknown=Planet(1,"Unknown",Position(45,0),observed=False,habitability=75)
    state=GameState(
        "no-colony-claim",2425,1,RaceProfile(),Tech(),[home,unknown],[],
        native={
            "design_profiles":[
                {"design_number":2,"name":"Mayflower","role":"colony","dry_mass":65,"fuel_capacity":200,"engine_id":4},
            ],
            "production_by_planet":{"0":[]},
        },
    )
    orders=OrderSet("no-colony-claim",2425,1)

    assert not [request for request in plan_objective_ship_builds(state) if request.role=="colony"]
    assert state.native["colony_pipeline"]["build_authorized"] is False
    add_economic_orders(state,orders)
    queue=next(order.payload["queue"] for order in orders.orders if order.kind=="set_planet_queue")
    assert not [item for item in queue if item.get("role")=="colony"]


def test_unfunded_sprint_is_downgraded_instead_of_reporting_zero_contributors(monkeypatch):
    capability=ResearchCapability(
        capability_id="test:short",name="Short capability",category="logistics",
        requirements={"construction":1},post_unlock_action="test",source="test",
    )
    demand=ResearchDemand(capability,1.0,1.0,1.0,1.0,False,"test")
    monkeypatch.setattr("stars_ai.research_planner._build_demands",lambda *_:[demand])
    monkeypatch.setattr("stars_ai.research_planner._score",lambda *_:100.0)
    state=GameState(
        "research",2425,1,RaceProfile(),Tech(),
        [Planet(0,"Tiny",Position(0,0),owner=1,population=10,observed=True)],[],
    )

    decision=plan_research(state)

    assert decision.posture!="SPRINT"
    assert decision.allocation_percent==15
    assert decision.contributor_planet_ids==()
    assert decision.sprint_executable is False
    assert decision.sprint_blocked_reason
    assert decision.commitment=="DEFERRED"
    assert decision.execution_status=="DEFERRED"
    assert decision.expected_resources==0
    orders=OrderSet("research",2425,1)
    add_economic_orders(state,orders,research_decision=decision)
    execution=state.native["research_sprint_execution"]
    assert execution["execution_status"]=="DEFERRED"
    assert execution["executable"] is False


def test_short_sprint_clears_routine_installations_for_a_real_contributor():
    home=Planet(
        0,"Home",Position(0,0),owner=1,observed=True,habitability=100,
        population=500_000,ironium=2_000,boranium=2_000,germanium=2_000,
        native={"is_homeworld":True},
    )
    state=GameState("defer",2425,1,RaceProfile(),Tech(),[home],[])
    decision=ResearchDecision(
        capability_id="test:short",capability_name="Short sprint",category="logistics",
        posture="SPRINT",current_field="construction",next_field="construction",
        allocation_percent=25,score=100.0,estimated_turns=1,estimate_confidence="high",
        requirements={"construction":1},remaining_requirements={"construction":1},
        contributor_planet_ids=(0,),protected_production_planet_ids=(),
        post_unlock_action="test",reason="test",expected_contributor_resources={0:500},
        commitment="SPRINT",execution_status="ACTIVE",expected_resources=300,
    )
    orders=OrderSet("defer",2425,1)

    add_economic_orders(state,orders,research_decision=decision)

    queue=next(order for order in orders.orders if order.kind=="set_planet_queue")
    assert queue.payload["clear_queue"] is True
    execution=state.native["research_sprint_execution"]
    assert execution["executable"] is True
    assert execution["expected_resources"]==300
    assert execution["expected_available_resource_total"]>0
    assert execution["contributors"]==[{"planet_id":0,"status":"contributing","reason":"queue cleared for named sprint"}]
    assert execution["deferred_installations"]
    assert any(
        "factory" in row["items"]
        for row in execution["factories_or_mines_deferred"]
    )


def test_late_execution_block_downgrades_the_serialized_sprint():
    decision=ResearchDecision(
        capability_id="test:blocked",capability_name="Blocked sprint",category="logistics",
        posture="SPRINT",current_field="construction",next_field="construction",
        allocation_percent=25,score=100.0,estimated_turns=1,estimate_confidence="high",
        requirements={"construction":1},remaining_requirements={"construction":1},
        contributor_planet_ids=(0,),protected_production_planet_ids=(0,),
        post_unlock_action="test",reason="test",sprint_executable=True,
        commitment="SPRINT",execution_status="ACTIVE",expected_resources=300,
    )
    state=GameState(
        "late-block",2425,1,RaceProfile(),Tech(),
        [Planet(0,"Home",Position(0,0),owner=1)],[],
        native={"research_sprint_execution":{"executable":False,"blocked_reason":"critical colony launch"}},
    )
    orders=OrderSet("late-block",2425,1)

    add_research_orders(state,orders,decision=decision)

    payload=next(order.payload for order in orders.orders if order.kind=="set_research")
    assert payload["posture"]=="RECOVERY"
    assert payload["allocation_percent"]==15
    assert payload["contributor_planet_ids"]==[]
    assert payload["commitment"]=="DEFERRED"
    assert payload["execution_status"]=="DEFERRED"


def test_recovery_high_without_expected_resources_is_deferred_before_production():
    decision=ResearchDecision(
        capability_id="test:recovery",capability_name="Emergency recovery",category="military",
        posture="MILITARY_EMERGENCY",current_field="weapons",next_field="weapons",
        allocation_percent=25,score=100.0,estimated_turns=2,estimate_confidence="high",
        requirements={"weapons":1},remaining_requirements={"weapons":1},
        contributor_planet_ids=(),protected_production_planet_ids=(),
        post_unlock_action="test",reason="test",sprint_executable=True,
        commitment="RECOVERY_HIGH",execution_status="ACTIVE",expected_resources=0,
    )
    state=GameState(
        "recovery-block",2425,1,RaceProfile(),Tech(),
        [Planet(0,"Home",Position(0,0),owner=1,observed=True,population=500_000)],[],
    )
    orders=OrderSet("recovery-block",2425,1)

    add_economic_orders(state,orders,research_decision=decision)
    execution=state.native["research_sprint_execution"]

    assert execution["commitment"]=="RECOVERY_HIGH"
    assert execution["execution_status"]=="DEFERRED"
    assert execution["executable"] is False
    assert "positive expected resources" in execution["blocked_reason"]


def test_missed_one_turn_sprint_emits_an_execution_failure_next_turn(monkeypatch):
    capability=ResearchCapability(
        capability_id="test:one-turn",name="One turn capability",category="logistics",
        requirements={"construction":1},post_unlock_action="test",source="test",
    )
    demand=ResearchDemand(capability,1.0,1.0,1.0,1.0,False,"test")
    monkeypatch.setattr("stars_ai.research_planner._build_demands",lambda *_:[demand])
    monkeypatch.setattr("stars_ai.research_planner._score",lambda *_:100.0)
    # Isolate the progress contract from hub-classification policy; this world
    # supplies a real positive production estimate in both turns.
    monkeypatch.setattr("stars_ai.research_planner._contributors",lambda *_:((0,),()))
    memory=AgentMemory()
    world=Planet(
        0,"Home",Position(0,0),owner=1,observed=True,habitability=100,
        population=500_000,factories=50,mines=50,
        ironium=2_000,boranium=2_000,germanium=2_000,
        native={"is_homeworld":True},
    )

    first=plan_research(
        GameState("one-turn",2425,1,RaceProfile(),Tech(),[world],[]),
        memory=memory,
    )
    assert first.commitment=="SPRINT"
    assert first.expected_resources>0
    assert first.estimated_turns==1

    # The following M-file reports the same tech, so the promised completion
    # did not happen.  The decision must no longer be an active sprint.
    repeat_world=Planet(
        0,"Home",Position(0,0),owner=1,observed=True,habitability=100,
        population=500_000,factories=50,mines=50,
        ironium=2_000,boranium=2_000,germanium=2_000,
        native={"is_homeworld":True},
    )
    second_state=GameState("one-turn",2426,1,RaceProfile(),Tech(),[repeat_world],[])
    second=plan_research(
        second_state,
        memory=memory,
    )

    assert second.commitment=="DEFERRED"
    assert second.execution_status=="DEFERRED"
    assert second.sprint_executable is False
    assert "Predicted one-turn sprint" in (second.execution_failure_reason or "")
    orders=OrderSet("one-turn",2426,1)
    add_economic_orders(second_state,orders,research_decision=second)
    execution=second_state.native["research_sprint_execution"]
    assert execution["execution_failure"] is True
    assert "Predicted one-turn sprint" in execution["blocked_reason"]


def test_idle_colony_uses_controlled_close_terraform_fallback_after_two_turns():
    race=RaceProfile(native={
        "hab_center":[50,50,50],"hab_low":[0,0,0],"hab_high":[100,100,100],
        "hab_immune":[False,False,False],
    })
    home=Planet(0,"Home",Position(0,0),owner=1,observed=True,habitability=100,population=200_000)
    # Current value 30%, eventual value 70%: no 50% current-quality claim
    # exists, but this is a close, positive bridge world after the idle limit.
    bridge=Planet(
        1,"Bridge",Position(40,0),observed=True,habitability=30,
        native={"environment":[5,25,35],"original_environment":[5,25,35]},
    )
    colony=Fleet(3,"Idle Colony",1,Position(0,0),role="colony",speed=7,
                 native={"position_object_id":0,"idle_colony_turns":2})
    state=GameState(
        "fallback",2424,1,race,
        Tech(biotechnology=25,energy=25,weapons=25,propulsion=25),
        [home,bridge],[colony],
    )
    orders=OrderSet("fallback",2424,1)

    add_economic_orders(state,orders)

    colonize=next(order for order in orders.orders if order.kind=="colony_operation")
    assert colonize.payload["destination_planet_id"]==1
    diagnostic=next(row for row in state.native["colony_target_diagnostics"] if row["planet_id"]==1)
    assert diagnostic["fallback_used"] is True
    assert diagnostic["hard_rejection_reason"] is None
    assert diagnostic["current_racial_hab"]==30
    assert diagnostic["eventual_hab"]==70


def test_foreign_colony_is_diagnosed_as_population_capture_not_colony_target():
    home=Planet(0,"Home",Position(0,0),owner=1,observed=True,habitability=100,population=200_000)
    enemy=Planet(1,"Enemy",Position(40,0),owner=2,observed=True,habitability=70,population=20_000)
    colony=Fleet(3,"Colony",1,Position(0,0),role="colony",speed=7,native={"position_object_id":0})
    state=GameState("capture",2425,1,RaceProfile(),Tech(),[home,enemy],[colony])
    orders=OrderSet("capture",2425,1)

    add_economic_orders(state,orders)

    assert not [order for order in orders.orders if order.kind=="colony_operation"]
    diagnostic=next(row for row in state.native["colony_target_diagnostics"] if row["planet_id"]==1)
    assert diagnostic["hard_rejection_reason"]=="foreign_owned_population_capture_not_colony"
    assert diagnostic["capture_desirable"] is True
    assert diagnostic["blocked_by"]=="transport_capacity"
    conversion=next(row for row in state.native["colony_capture_conversions"] if row["planet_id"]==1)
    assert conversion["action"]=="POPULATION_CAPTURE_EVALUATION"
    assert conversion["status"]=="NEEDS_CAPTURE_TRANSPORT"


def test_idle_colony_uses_second_best_valid_claim_when_best_is_reserved():
    home=Planet(0,"Home",Position(0,0),owner=1,observed=True,habitability=100,population=200_000)
    best=Planet(1,"Reserved",Position(35,0),observed=True,habitability=90)
    second=Planet(2,"Second Best",Position(45,0),observed=True,habitability=70)
    idle=Fleet(3,"Idle Colony",1,Position(0,0),role="colony",speed=7,native={"position_object_id":0})
    committed=Fleet(4,"Committed Colony",1,Position(35,0),role="colony",speed=7,destination_planet_id=1)
    state=GameState("reserved-fallback",2425,1,RaceProfile(),Tech(),[home,best,second],[idle,committed])
    orders=OrderSet("reserved-fallback",2425,1)

    add_economic_orders(state,orders)

    colonize=next(order for order in orders.orders if order.kind=="colony_operation")
    assert colonize.payload["fleet_id"]==3
    assert colonize.payload["destination_planet_id"]==2


def test_colony_without_any_valid_claim_is_explicitly_reserved_for_repurpose():
    home=Planet(0,"Home",Position(0,0),owner=1,observed=True,habitability=100,population=200_000)
    colony=Fleet(3,"No Target Colony",1,Position(0,0),role="colony",speed=7,native={"position_object_id":0,"idle_colony_turns":3})
    state=GameState("repurpose",2425,1,RaceProfile(),Tech(),[home],[colony])
    orders=OrderSet("repurpose",2425,1)

    add_economic_orders(state,orders)

    intent=state.native["colony_repurpose_intents"]
    assert intent==[{
        "fleet_id":3,
        "action":"COLONY_RESERVE_REPURPOSE",
        "idle_turns":3,
        "destination_planet_id":0,
        "reason":"No unreserved, currently viable colony claim remains; reserve this hull for the next valid claim or a separately approved population-capture transport program.",
    }]
    assert not [order for order in orders.orders if order.kind=="colony_operation"]
    report=ensure_fleet_activity(state,orders)
    assert report[0]["action"]=="COLONY RESERVE / REPURPOSE"


def test_public_major_tech_gap_adds_modest_research_pressure():
    state=GameState(
        "tech-gap",2425,1,RaceProfile(),Tech(energy=2,weapons=2,propulsion=2,construction=2,electronics=2,biotechnology=2),
        [Planet(0,"Home",Position(0,0),owner=1)],[],
        native={"score_visibility":"public","player_scores":[
            {"player_id":1,"tech_total":12},{"player_id":7,"tech_total":23},
        ]},
    )

    pressure=_competitive_tech_pressure(state)

    assert pressure["tech_gap"]==11
    assert pressure["research_pressure"]>1.0
    assert pressure["score_bonus"]>0
