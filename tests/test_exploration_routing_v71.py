
from stars_ai.models import GameState,RaceProfile,Tech,Planet,Fleet,Position,OrderSet
from stars_ai.memory import AgentMemory
from stars_ai.exploration_router import build_probe_route, exploration_promotion_target
from stars_ai.fuel_planner import (
    highest_zero_fuel_warp,
    scout_one_way_warp,
    apply_fuel_safety,
)
from stars_ai.strategy.exploration import add_exploration_orders
from stars_ai.objective_production import _desired_scout_force,_desired_colony_force


def _fuel_profile(engine_id=2,fuel=10,capacity=50,mass=20):
    return {
        "groups":[{
            "mass":mass,
            "engine_id":engine_id,
            "engine_name":"Fuel Mizer" if engine_id==2 else "Daddy Long Legs 7",
        }],
        "fuel":fuel,
        "effective_fuel":fuel,
        "fuel_capacity":capacity,
        "mass":mass,
        "dry_mass":mass,
        "cargo_mass":0,
        "all_ram_scoop":False,
    }


def _state(n=40,year=2410):
    planets=[
        Planet(
            i,f"P{i}",Position(float(i*15),float((i%3)*12)),
            owner=(1 if i==0 else None),
            observed=(i==0),
            habitability=(80 if i==0 else None),
        )
        for i in range(n)
    ]
    return GameState(
        "g",year,1,RaceProfile(native={"lrts":["IFE"]}),Tech(),planets,[],
        native={"strategic_watchdog":{
            "exploration_pressure":1.75,
            "explored_count":10,
            "discoveries_last_5_turns":5,
            "milestone":{
                "deadline_turn":15,
                "explored_optimal":35,
            },
        }},
    )


def test_fuel_mizer_has_free_warp4():
    fp=_fuel_profile(engine_id=2,fuel=0,capacity=50)
    assert highest_zero_fuel_warp(fp)==4
    assert scout_one_way_warp(fp,60,ife=True,ce=False,pressure=1.0)==4


def test_low_fuel_mizer_scan_is_not_sent_home_to_refuel():
    s=_state(10,year=2420)
    scout=Fleet(
        0,"Probe",1,Position(0,0),role="scout",
        native={
            "fuel_profile":_fuel_profile(engine_id=2,fuel=0,capacity=50),
            "race_fuel_flags":{"ife":True,"ce":False},
        },
    )
    s.fleets=[scout]
    # Give P1 an actual refueling starbase, which old logic would prefer.
    s.planets[0].native={
        "starbase_capabilities":{"can_refuel":True}
    }

    o=OrderSet("g",2420,1)
    o.add(
        "move_fleet",
        {
            "fleet_id":0,
            "destination_planet_id":2,
            "warp":9,
            "mission":"scan",
        },
        "probe outward",
        priority=100,
    )
    apply_fuel_safety(s,o)

    assert len(o.orders)==1
    move=o.orders[0]
    assert move.payload["mission"]=="scan"
    assert move.payload["destination_planet_id"]==2
    assert move.payload["warp"]==4
    assert move.payload["fuel_plan"]["policy"]=="fastest_fuel_safe_mizer_recon"


def test_probe_route_can_plan_twelve_unique_forward_worlds():
    s=_state(30)
    scout=Fleet(
        0,"Probe",1,Position(0,0),role="scout",
        native={
            "fuel_profile":_fuel_profile(engine_id=2,fuel=100,capacity=100),
            "race_fuel_flags":{"ife":True,"ce":False},
        },
    )
    s.fleets=[scout]
    route=build_probe_route(
        s,scout,[p for p in s.planets if not p.observed],
        pressure=1.75,max_stops=12,
    )
    assert route is not None
    assert len(route.planet_ids)==12
    assert len(set(route.planet_ids))==12
    assert route.expected_discoveries==12
    assert route.terminal is True


def test_probe_route_stays_with_the_previous_waypoints_local_unexplored_cluster():
    home=Planet(0,"Home",Position(0,0),owner=1,observed=True,habitability=80)
    local=[
        Planet(1,"Local A",Position(20,0),observed=False),
        Planet(2,"Local B",Position(45,0),observed=False),
    ]
    # A dense distant group has a larger aggregate cluster score, but it must
    # not make the scout skip the two nearby unknowns.
    distant=[
        Planet(10+i,f"Distant {i}",Position(200+i*4,(i%2)*4),observed=False)
        for i in range(8)
    ]
    scout=Fleet(0,"Probe",1,Position(0,0),role="scout")
    state=GameState("g",2410,1,RaceProfile(),Tech(),[home,*local,*distant],[scout])

    route=build_probe_route(state,scout,[*local,*distant],max_stops=2)

    assert route is not None
    assert route.planet_ids == [1,2]


def test_persistent_route_is_reused_next_turn_instead_of_replanned():
    s=_state(20,year=2410)
    scout=Fleet(
        0,"Probe",1,Position(0,0),role="scout",
        native={
            "fuel_profile":_fuel_profile(engine_id=2,fuel=100,capacity=100),
            "race_fuel_flags":{"ife":True,"ce":False},
        },
    )
    s.fleets=[scout]
    mem=AgentMemory()
    mem.set_scout_route(0,[4,5,6,7],2410,expected_discoveries=4,total_distance=100)

    o=OrderSet("g",2410,1)
    add_exploration_orders(s,o,None,memory=mem)

    scan=next(x for x in o.orders if x.payload.get("mission")=="scan")
    assert scan.payload["destination_planet_id"]==4
    assert scan.payload["route_planet_ids"][:4]==[4,5,6,7]
    assert scan.payload["route_managed"] is True


def test_milestone_deficit_can_raise_scout_force_above_old_cap():
    s=_state(288,year=2440)
    s.native["strategic_watchdog"]={
        "exploration_pressure":1.75,
        "explored_count":45,
        "discoveries_last_5_turns":5,
        "milestone":{
            "deadline_turn":55,
            "explored_optimal":193,
        },
    }
    desired,reason=_desired_scout_force(s,None,current_scout_assets=3)
    assert desired>6
    assert desired<=24
    assert "target scout force" in reason


def test_colony_force_is_bounded_by_population_export_capacity():
    s=_state(20,year=2410)
    # Many known viable claims, but homeworld cannot safely export even one
    # validated 25-kT / 2,500-colonist colony packet above the opening reserve.
    for p in s.planets[1:11]:
        p.observed=True
        p.habitability=80
    s.planets[0].population=12000

    desired,reason=_desired_colony_force(
        s,[p for p in s.planets if p.owner is None and p.observed],None
    )
    assert desired<=1
    assert "25 kT / 2,500 colonists" in reason


def test_exploration_order_contains_route_diagnostics():
    s=_state(30,year=2410)
    scout=Fleet(
        0,"Probe",1,Position(0,0),role="scout",
        native={
            "fuel_profile":_fuel_profile(engine_id=2,fuel=100,capacity=100),
            "race_fuel_flags":{"ife":True,"ce":False},
        },
    )
    s.fleets=[scout]
    mem=AgentMemory()
    o=OrderSet("g",2410,1)
    add_exploration_orders(s,o,None,memory=mem)
    scan=next(x for x in o.orders if x.payload.get("mission")=="scan")
    assert scan.payload["route_remaining"]>=1
    assert scan.payload["route_expected_discoveries"]>=1
    assert scan.payload["route_terminal"] is True
    assert scan.payload["free_cruise_warp"]==4
    assert scan.payload["promotion_target"]["label"].startswith("P")


def test_promotion_targets_identify_successive_p1_to_p4_relay_rings():
    """Unknown systems receive a ring purpose before their value is known."""
    home=Planet(0,"Home",Position(0,0),owner=1,observed=True,habitability=100,
                population=500_000,native={"is_homeworld":True})
    p1=Planet(1,"P1",Position(130,0),owner=1,observed=True,habitability=85,population=80_000)
    p2=Planet(2,"P2",Position(260,0),owner=1,observed=True,habitability=80,population=20_000)
    p3=Planet(3,"P3",Position(390,0),owner=1,observed=True,habitability=75,population=10_000)
    p1_candidate=Planet(10,"P1 Candidate",Position(115,30),observed=False)
    p2_candidate=Planet(11,"P2 Candidate",Position(250,30),observed=False)
    p3_candidate=Planet(12,"P3 Candidate",Position(385,30),observed=False)
    p4_candidate=Planet(13,"P4 Candidate",Position(520,30),observed=False)
    state=GameState(
        "rings",2410,1,RaceProfile(),Tech(),
        [home,p1,p2,p3,p1_candidate,p2_candidate,p3_candidate,p4_candidate],[],
    )

    assert exploration_promotion_target(state,p1_candidate)["label"]=="P1"
    assert exploration_promotion_target(state,p2_candidate)["label"]=="P2"
    assert exploration_promotion_target(state,p3_candidate)["label"]=="P3"
    assert exploration_promotion_target(state,p4_candidate)["label"]=="P4"
