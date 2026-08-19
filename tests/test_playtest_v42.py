
from stars_ai.playtest import *
from stars_ai.models import *

def test_default_playtest_has_four_players_and_checkpoints():
    c=default_playtest_config()
    assert len(c.players)==4
    assert c.checkpoints==[10,25,50]
    assert c.players[2].prt=="SS"
    assert c.players[3].prt=="SD"

def test_metrics_from_state():
    s=GameState(
        "g",2410,1,RaceProfile(primary_trait="JOAT"),Tech(energy=1,weapons=2,propulsion=3,construction=4,electronics=5,biotechnology=6),
        [
            Planet(1,"Home",Position(0,0),owner=1,population=500000,factories=100,mines=80,native={"starbase":True,"gate":True}),
            Planet(2,"Colony",Position(20,0),owner=1,population=100000,factories=10,mines=20),
        ],
        [Fleet(1,"F",1,Position(0,0),role="combat",native={"ship_count":5,"design_slots":[1,2]})]
    )
    m=metrics_from_state(s,10)
    assert m.planets==2
    assert m.population==600000
    assert m.tech_sum==21
    assert m.starbases==1
    assert m.gates==1
    assert m.ships==5
    assert m.design_slots_used==2

def test_intent_actual_mismatch_for_validated_order():
    i=IntentRecord(5,1,"movement","fleet:1","moved to Vega","VALIDATED","attack")
    a=ActualRecord(5,1,"movement","fleet:1","remained at Sol")
    m=compare_intent_to_actual(i,a)
    assert m is not None
    assert m.severity=="HIGH"

def test_matching_intent_actual_is_ok():
    i=IntentRecord(5,1,"movement","fleet:1","moved to Vega","VALIDATED","attack")
    a=ActualRecord(5,1,"movement","fleet:1","moved and arrived at Vega")
    assert compare_intent_to_actual(i,a) is None
