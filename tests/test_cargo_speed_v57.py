
from stars_ai.models import Fleet,Position,Planet,GameState,RaceProfile,Tech,OrderSet
from stars_ai.warp_policy import mission_warp
from stars_ai.strategy.economy import add_economic_orders

def test_old_observed_warp_does_not_cap_mission_warp():
    f=Fleet(0,"Remote Miner",1,Position(0,0),role="miner",speed=8,native={"observed_warp":2})
    assert mission_warp(f,Position(100,0),"reposition_for_remote_mining")==8

def test_short_miner_leg_can_use_7():
    f=Fleet(0,"Remote Miner",1,Position(0,0),role="miner",speed=8)
    assert mission_warp(f,Position(20,0),"reposition_for_remote_mining")==7

def test_colony_load_decision_surfaces_diagnostics():
    planets=[
        Planet(0,"Home",Position(1000,1000),owner=1,observed=True,habitability=100,population=250000),
        Planet(1,"Green",Position(1030,1000),owner=None,observed=True,habitability=80),
    ]
    fleets=[Fleet(0,"Colony",1,Position(1000,1000),role="colony",speed=8,
                  cargo_population=0,native={"position_object_id":0})]
    s=GameState("g",2400,1,RaceProfile(),Tech(),planets,fleets)
    o=OrderSet("g",2400,1)
    add_economic_orders(s,o,None)
    c=next(x for x in o.orders if x.kind=="colony_operation")
    assert c.payload["load_25kt_population"] is True
    assert c.payload["cargo_population_before"]==0
    assert c.payload["source_population"]==250000
    assert c.payload["warp"] in (7,8)

def test_colony_source_coordinate_fallback():
    planets=[
        Planet(5,"Home",Position(1000,1000),owner=1,observed=True,habitability=100,population=250000),
        Planet(6,"Green",Position(1040,1000),owner=None,observed=True,habitability=80),
    ]
    # no position_object_id, source must resolve from coordinates
    fleets=[Fleet(2,"Colony",1,Position(1000,1000),role="colony",speed=8,cargo_population=0,native={})]
    s=GameState("g",2400,1,RaceProfile(),Tech(),planets,fleets)
    o=OrderSet("g",2400,1); add_economic_orders(s,o,None)
    c=next(x for x in o.orders if x.kind=="colony_operation")
    assert c.payload["source_planet_id"]==5
    assert c.payload["load_25kt_population"] is True
