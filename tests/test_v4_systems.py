
from stars_ai.models import *
from stars_ai.empire_optimizer import classify_planet_role, optimize_population_transfers
from stars_ai.base_network import evaluate_base_network
from stars_ai.invasion import plan_invasion
from stars_ai.logistics import plan_route, RouteMode
from stars_ai.counter_design import generate_counter_doctrine
from stars_ai.intelligence import estimate_stale_value
from stars_ai.race_doctrine import doctrine_for
from stars_ai.native_capabilities import may_emit_native, capability
from stars_ai.strategic_lookahead import StrategicOption, choose_strategy

def state():
    ps=[
      Planet(1,"Home",Position(0,0),owner=1,habitability=90,population=600000,factories=300,mines=200,native={"capacity_population":1000000,"starbase":True,"gate":True,"strategic_value":1.0}),
      Planet(2,"Young",Position(50,0),owner=1,habitability=80,population=50000,factories=10,mines=10,native={"capacity_population":1000000,"strategic_value":0.6}),
      Planet(3,"Enemy",Position(180,0),owner=2,habitability=70,population=300000,factories=150,mines=100,defenses=30,native={"strategic_value":0.8}),
    ]
    fs=[Fleet(1,"Freighter",1,Position(0,0),role="freighter",cargo_capacity=25,speed=9,native={"fuel_range_ly":100}),
        Fleet(2,"EnemyFleet",2,Position(170,0),role="combat",combat_power=100)]
    return GameState("x",2400,1,RaceProfile(name="R",primary_trait="IT"),Tech(),ps,fs)

def test_empire_optimizer_and_base_network():
    s=state()
    assert classify_planet_role(s.planets[1]).role in ("BREEDER","DEVELOPING")
    transfers=optimize_population_transfers(s)
    assert transfers
    assert transfers[0].population==2500
    bases=evaluate_base_network(s)
    assert bases and bases[0].priority>0

def test_invasion_and_logistics():
    s=state()
    inv=plan_invasion(s.planets[2],our_local_strength=200,enemy_local_strength=100,transport_capacity=500000)
    assert inv.action in ("CAPTURE","NEUTRALIZE","BOMB_OR_BYPASS","BYPASS")
    r=plan_route(s,s.fleets[0],s.planets[2],fuel_range_ly=100)
    assert r.mode in RouteMode

def test_counter_design():
    class P:
        beam_strength=0; torpedo_strength=100; shield_strength=100; armor_strength=100
        resource_cost=10; effective_combat_value=2
    d=generate_counter_doctrine([P(),P()])
    assert d.preferred_weapon=="beam"

def test_intel_uncertainty_expands():
    e=estimate_stale_value("fleet",100,10,20,.1)
    assert e.estimated_high>100
    assert e.confidence<1

def test_race_doctrine():
    assert doctrine_for("IT").objective_modifiers["gates"]>1
    assert doctrine_for("PP").objective_modifiers["packets"]>1

def test_native_safety():
    assert may_emit_native("move_fleet")
    assert not may_emit_native("create_design")
    assert capability("player_relation_change").status=="PARTIAL"

def test_lookahead_can_prefer_future():
    a=StrategicOption("attack",2,.8,.2,.3,.4,.0,.1,.6)
    b=StrategicOption("tech",4,.1,1.0,.1,.05,.9,.2,.3)
    d=choose_strategy([a,b],risk_tolerance=.4)
    assert d.selected.name=="tech"
