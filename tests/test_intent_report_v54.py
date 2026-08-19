
from stars_ai.models import (
    GameState,RaceProfile,Tech,Planet,Fleet,Position,OrderSet
)
from stars_ai.fleet_intent import ensure_fleet_activity

def _base():
    planets=[
        Planet(0,"Home",Position(0,0),owner=1,observed=True,habitability=100),
        Planet(1,"Green",Position(20,0),owner=None,observed=True,habitability=80),
        Planet(2,"Unknown",Position(30,0),owner=None,observed=False),
    ]
    fleets=[
        Fleet(0,"Scout 1",1,Position(0,0),role="scout",speed=7),
        Fleet(1,"Colony 1",1,Position(0,0),role="colony",speed=7),
        Fleet(2,"Escort 1",1,Position(0,0),role="combat",combat_power=10,speed=7),
    ]
    return GameState("g",2400,1,RaceProfile(),Tech(),planets,fleets)

def test_every_fleet_gets_explicit_intent():
    s=_base(); o=OrderSet("g",2400,1)
    intents=ensure_fleet_activity(s,o)
    assert {x["fleet_id"] for x in intents} == {0,1,2}
    assert all(x["action"] for x in intents)

def test_idle_scout_gets_move_not_silent_hold():
    s=_base(); o=OrderSet("g",2400,1)
    intents=ensure_fleet_activity(s,o)
    scout=next(x for x in intents if x["fleet_id"]==0)
    assert scout["action"] in ("RECON","MOVE")
    assert any(
        x.kind=="move_fleet" and x.payload["fleet_id"]==0
        for x in o.orders
    )

def test_idle_colony_does_not_move_without_complete_colony_operation():
    s=_base(); o=OrderSet("g",2400,1)
    intents=ensure_fleet_activity(s,o)
    colony=next(x for x in intents if x["fleet_id"]==1)
    assert colony["action"]=="HOLD / COLONY READY"
    assert colony["destination_planet_id"] is None
    assert not any(x.kind=="move_fleet" and x.payload.get("fleet_id")==1 for x in o.orders)

def test_armed_combat_hold_is_explicit():
    s=_base(); o=OrderSet("g",2400,1)
    intents=ensure_fleet_activity(s,o)
    combat=next(x for x in intents if x["fleet_id"]==2)
    assert combat["action"]=="HOLD / DEFEND"
    assert "defense" in combat["reason"].lower()

def test_existing_waypoint_is_explicit_continue():
    s=_base()
    s.fleets[0].destination_planet_id=2
    o=OrderSet("g",2400,1)
    intents=ensure_fleet_activity(s,o)
    scout=next(x for x in intents if x["fleet_id"]==0)
    assert scout["action"]=="CONTINUE WAYPOINT"
