
from stars_ai.models import GameState,RaceProfile,Tech,Planet,Fleet,Position,OrderSet
from stars_ai.strategy.exploration import add_exploration_orders,deconflict_recon_orders

def _state():
    planets=[
        Planet(0,"Home",Position(0,0),owner=1,observed=True),
        Planet(1,"A",Position(20,0),owner=None,observed=False),
        Planet(2,"B",Position(22,0),owner=None,observed=False),
        Planet(3,"C",Position(30,0),owner=None,observed=False),
    ]
    fleets=[
        Fleet(0,"Scout 1",1,Position(0,0),role="scout"),
        Fleet(1,"Scout 2",1,Position(0,0),role="scout"),
    ]
    return GameState("g",2400,1,RaceProfile(),Tech(),planets,fleets)

def test_two_idle_scouts_receive_unique_unknown_planets():
    s=_state(); o=OrderSet("g",2400,1)
    add_exploration_orders(s,o,None)
    targets=[
        x.payload["destination_planet_id"]
        for x in o.orders
        if x.kind=="move_fleet" and x.payload.get("mission")=="scan"
    ]
    assert len(targets)==2
    assert len(set(targets))==2

def test_active_scout_destination_is_reserved_for_other_scouts():
    s=_state()
    s.fleets[0].destination_planet_id=1
    o=OrderSet("g",2400,1)
    add_exploration_orders(s,o,None)
    targets=[
        x.payload["destination_planet_id"]
        for x in o.orders
        if x.kind=="move_fleet" and x.payload.get("mission")=="scan"
    ]
    assert targets
    assert 1 not in targets

def test_final_barrier_retargets_duplicate_scan_orders():
    s=_state(); o=OrderSet("g",2400,1)
    o.add("move_fleet",{"fleet_id":0,"destination_planet_id":1,"warp":7,"mission":"scan"},"first",priority=100)
    o.add("move_fleet",{"fleet_id":1,"destination_planet_id":1,"warp":7,"mission":"scan"},"duplicate",priority=90)
    deconflict_recon_orders(s,o)
    scans=[
        x for x in o.orders
        if x.kind=="move_fleet" and x.payload.get("mission")=="scan"
    ]
    assert len(scans)==2
    assert len({x.payload["destination_planet_id"] for x in scans})==2
