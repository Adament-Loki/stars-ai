
from stars_ai.models import *
from stars_ai.agent import StarsAgent

def test_agent_emits_v4_assessment_notes():
    s=GameState(
      "g",2400,1,RaceProfile(primary_trait="IT"),Tech(),
      [Planet(1,"Home",Position(0,0),owner=1,habitability=90,population=500000,factories=100,mines=100,native={"capacity_population":1000000,"starbase":True,"gate":True}),
       Planet(2,"Young",Position(40,0),owner=1,habitability=80,population=50000,native={"capacity_population":1000000})],
      [Fleet(1,"F",1,Position(0,0),role="freighter",cargo_capacity=50000)]
    )
    o=StarsAgent(s).play_turn()
    text="\n".join(o.notes)
    assert "v4 strategic lookahead" in text
    assert "v4 race doctrine" in text
    assert "native safety" in text
