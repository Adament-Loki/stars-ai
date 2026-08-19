
from stars_ai.models import *
from stars_ai.logistics import (
    plan_route, RouteMode, planet_concealment_value, route_detection_risk
)
from stars_ai.intelligence import penetrating_scanner_probability

def make_state():
    planets=[
      Planet(1,"Home",Position(0,0),owner=1,native={"starbase":True}),
      Planet(2,"Relay",Position(70,10),owner=1,native={"starbase":True,"strategic_value":0.8}),
      Planet(3,"Target",Position(150,0),owner=2),
    ]
    fleets=[Fleet(1,"Strike",1,Position(0,0),role="combat",speed=9,native={"fuel_range_ly":90})]
    return GameState("x",2400,1,RaceProfile(),Tech(),planets,fleets)

def test_planet_concealment_collapses_with_penetrating_scanner():
    p=Planet(1,"P",Position(0,0),owner=1,native={"strategic_value":1.0})
    assert planet_concealment_value(p,enemy_penetrating_scanner_probability=0.0) > 0.9
    assert planet_concealment_value(p,enemy_penetrating_scanner_probability=1.0) == 0.0

def test_starbase_planet_is_preferred_as_refuel_and_cover_waypoint():
    s=make_state()
    r=plan_route(
        s,s.fleets[0],s.planets[2],
        fuel_range_ly=90,
        enemy_penetrating_scanner_probability=0.1,
        stealth_priority=0.8
    )
    assert r.mode in (RouteMode.PLANET_HOP,RouteMode.REFUEL)
    assert 2 in r.path_planet_ids
    assert 2 in r.refuel_stops
    assert 2 in r.concealment_stops

def test_known_penetrating_scanner_reduces_reason_to_detour_for_cover():
    s=make_state()
    # Make direct route feasible so stealth is the deciding factor.
    r=plan_route(
        s,s.fleets[0],s.planets[2],
        fuel_range_ly=200,
        enemy_penetrating_scanner_probability=0.99,
        stealth_priority=0.8
    )
    # A starbase may still be used for operational reasons, but cover is nearly worthless.
    assert r.detection_risk >= 0.7 or r.mode == RouteMode.DIRECT

def test_scanner_probability_uses_known_capability_and_staleness():
    assert penetrating_scanner_probability(known_penetrating_scanner=True) > 0.95
    a=penetrating_scanner_probability(last_scanner_observation_turn=10,current_turn=10)
    b=penetrating_scanner_probability(last_scanner_observation_turn=10,current_turn=20)
    assert b>a
