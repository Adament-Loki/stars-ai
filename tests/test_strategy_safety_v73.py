from stars_ai.colony_planner import score_colony_candidates
from stars_ai.models import Fleet,GameState,OrderSet,Planet,Position,RaceProfile,Tech
from stars_ai.strategy.economy import add_economic_orders
from stars_ai.strategy.exploration import add_exploration_orders


def _state(planets,fleets,year=2420):
    return GameState(
        "g",year,1,RaceProfile(),Tech(),planets,fleets,
        native={"strategic_watchdog":{"exploration_pressure":1.0}},
    )


def test_explored_persistent_intel_remains_a_colony_target_without_rescouting():
    planets=[
        Planet(0,"Home",Position(0,0),owner=1,observed=True,population=300000),
        Planet(
            1,"Stale",Position(40,0),owner=None,observed=True,habitability=90,
            native={"intel_source":"persistent_memory","intel_age_years":7},
        ),
    ]
    fleet=Fleet(0,"Colony",1,Position(0,0),role="colony")
    candidates=score_colony_candidates(_state(planets,[fleet]),fleet)
    assert [candidate.planet_id for candidate in candidates]==[1]


def test_negative_value_distant_colony_is_rejected():
    planets=[
        Planet(0,"Home",Position(0,0),owner=1,observed=True,population=300000),
        Planet(1,"Marginal",Position(280,0),owner=None,observed=True,habitability=25),
    ]
    fleet=Fleet(0,"Colony",1,Position(0,0),role="colony")
    assert score_colony_candidates(_state(planets,[fleet]),fleet)==[]


def test_empty_colony_ship_leaves_low_population_owned_world_for_loading_base():
    planets=[
        Planet(0,"Home",Position(0,0),owner=1,observed=True,population=300000),
        Planet(1,"Weak",Position(50,0),owner=1,observed=True,population=15700),
        Planet(2,"Green",Position(80,0),owner=None,observed=True,habitability=85),
    ]
    fleet=Fleet(0,"Colony",1,Position(50,0),role="colony",cargo_population=0)
    state=_state(planets,[fleet])
    orders=OrderSet("g",state.year,1)
    add_economic_orders(state,orders,None)
    move=next(x for x in orders.orders if x.kind=="move_fleet")
    assert move.payload["mission"]=="return_for_colonists"
    assert move.payload["destination_planet_id"]==0


def test_exploration_route_stays_inside_owned_support_radius():
    planets=[
        Planet(0,"Home",Position(0,0),owner=1,observed=True),
        Planet(1,"Near",Position(100,0),observed=False),
        Planet(2,"Edge",Position(290,0),observed=False),
        Planet(3,"Too Far",Position(450,0),observed=False),
    ]
    fleet=Fleet(0,"Probe",1,Position(0,0),role="scout")
    state=_state(planets,[fleet])
    orders=OrderSet("g",state.year,1)
    add_exploration_orders(state,orders,None)
    scan=next(x for x in orders.orders if x.payload.get("mission")=="scan")
    assert 3 not in scan.payload["route_planet_ids"]
    assert len(scan.payload["route_waypoints"])==len(scan.payload["route_planet_ids"])
    assert len(scan.payload["route_waypoints"])<=7
