
from stars_ai.models import GameState,RaceProfile,Tech,Planet,Fleet,Position,OrderSet
from stars_ai.strategy.economy import add_economic_orders
from stars_ai.fleet_intent import ensure_fleet_activity

def state(fleets, planets=None):
    planets=planets or [
        Planet(0,"Home",Position(0,0),owner=1,observed=True,habitability=100,population=300000),
        Planet(1,"Unknown",Position(40,0),owner=None,observed=False,habitability=None),
    ]
    return GameState("g",2400,1,RaceProfile(),Tech(),planets,fleets)

def test_colony_stays_home_when_only_unknown_worlds_exist():
    s=state([Fleet(2,"Colony",1,Position(0,0),role="colony",cargo_population=0,speed=8)])
    o=OrderSet("g",2400,1); add_economic_orders(s,o,None)
    intents=ensure_fleet_activity(s,o)
    assert not any(x.kind=="move_fleet" and x.payload.get("fleet_id")==2 for x in o.orders)
    assert next(x for x in intents if x["fleet_id"]==2)["action"]=="HOLD FOR COLONY INTEL"

def test_freighter_holds_without_real_logistics_route():
    s=state([Fleet(3,"Cargo",1,Position(0,0),role="freighter",speed=8)])
    o=OrderSet("g",2400,1); add_economic_orders(s,o,None)
    intents=ensure_fleet_activity(s,o)
    assert not any(x.kind=="move_fleet" and x.payload.get("fleet_id")==3 for x in o.orders)
    assert next(x for x in intents if x["fleet_id"]==3)["action"]=="HOLD FOR LOGISTICS"

def test_remote_miner_does_not_treat_unknown_planet_as_mining_target():
    s=state([Fleet(5,"Miner",1,Position(0,0),role="miner",speed=8)])
    o=OrderSet("g",2400,1)
    intents=ensure_fleet_activity(s,o)
    assert not any(x.kind=="move_fleet" and x.payload.get("fleet_id")==5 for x in o.orders)
    assert next(x for x in intents if x["fleet_id"]==5)["action"]=="HOLD FOR MINING TARGET"

def test_remote_miner_moves_only_to_observed_mineral_target():
    planets=[
        Planet(0,"Home",Position(0,0),owner=1,observed=True,habitability=100,population=300000),
        Planet(1,"Rock",Position(30,0),owner=None,observed=True,habitability=-5,
               native={"mineral_concentrations":[70,50,60]}),
    ]
    s=state([Fleet(5,"Miner",1,Position(0,0),role="miner",speed=8)],planets)
    o=OrderSet("g",2400,1)
    intents=ensure_fleet_activity(s,o)
    move=next(x for x in o.orders if x.kind=="move_fleet" and x.payload.get("fleet_id")==5)
    assert move.payload["destination_planet_id"]==1
    assert move.payload["mission"]=="reposition_for_remote_mining"

def test_known_viable_colony_at_home_triggers_load_plus_colonize():
    planets=[
        Planet(0,"Home",Position(0,0),owner=1,observed=True,habitability=100,population=300000),
        Planet(1,"Green",Position(50,0),owner=None,observed=True,habitability=80),
    ]
    s=state([Fleet(2,"Colony",1,Position(0,0),role="colony",cargo_population=0,speed=8)],planets)
    o=OrderSet("g",2400,1); add_economic_orders(s,o,None)
    c=next(x for x in o.orders if x.kind=="colony_operation")
    assert c.payload["load_25kt_population"] is True
    assert c.payload["destination_planet_id"]==1

def test_empty_colony_away_from_home_returns_for_population():
    planets=[
        Planet(0,"Home",Position(0,0),owner=1,observed=True,habitability=100,population=300000),
        Planet(1,"Green",Position(50,0),owner=None,observed=True,habitability=80),
    ]
    s=state([Fleet(2,"Colony",1,Position(25,25),role="colony",cargo_population=0,speed=8)],planets)
    o=OrderSet("g",2400,1); add_economic_orders(s,o,None)
    move=next(x for x in o.orders if x.kind=="move_fleet" and x.payload.get("fleet_id")==2)
    assert move.payload["mission"]=="return_for_colonists"
    assert move.payload["destination_planet_id"]==0
