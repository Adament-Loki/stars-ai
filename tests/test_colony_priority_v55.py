
from stars_ai.models import GameState,RaceProfile,Tech,Planet,Fleet,Position,OrderSet
from stars_ai.colony_planner import score_colony_candidates
from stars_ai.fleet_intent import ensure_fleet_activity

def _state():
    planets=[
        Planet(0,"Home",Position(0,0),owner=1,observed=True,habitability=100),
        Planet(1,"ExcellentNear",Position(25,0),owner=None,observed=True,habitability=85),
        Planet(2,"ExcellentFar",Position(120,0),owner=None,observed=True,habitability=90),
        Planet(3,"MarginalNear",Position(15,0),owner=None,observed=True,habitability=40),
    ]
    fleets=[Fleet(0,"Santa Maria",1,Position(0,0),role="colony",speed=7)]
    return GameState("g",2400,1,RaceProfile(),Tech(),planets,fleets)

def test_colony_scoring_prefers_strong_near_world():
    s=_state()
    ranked=score_colony_candidates(s,s.fleets[0])
    assert ranked[0].planet_name=="ExcellentNear"
    assert ranked[0].score > ranked[1].score

def test_colony_intent_contains_ranked_candidates_but_does_not_move_empty():
    s=_state(); o=OrderSet("g",2400,1)
    intents=ensure_fleet_activity(s,o)
    c=next(x for x in intents if x["role"]=="colony")
    assert c["action"]=="HOLD / COLONY READY"
    assert c["destination_planet_id"] is None
    assert len(c["colony_candidates"])==2
    assert c["colony_candidates"][0]["planet_name"]=="ExcellentNear"
    assert not any(x.kind=="move_fleet" and x.payload.get("fleet_id")==0 for x in o.orders)

def test_colony_activity_invariant_is_purpose_not_motion():
    s=_state(); o=OrderSet("g",2400,1)
    intents=ensure_fleet_activity(s,o)
    c=next(x for x in intents if x["role"]=="colony")
    assert c["action"].startswith("HOLD")
