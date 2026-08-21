from stars_ai.models import Fleet, GameState, Planet, Position, RaceProfile, Tech
from stars_ai.research_planner import _build_demands, _nearby_threats


def _state():
    home=Planet(0,"Home",Position(0,0),owner=1,population=100_000)
    target=Planet(
        1,"Rich Rock",Position(80,0),owner=None,observed=True,
        native={"mineral_concentrations":[100,100,100]},
    )
    return GameState(
        "research",2449,1,
        RaceProfile(native={"prt_id":7,"lrts":["IFE"]}),
        Tech(construction=5,electronics=0),[home,target],[],
        native={"design_profiles":[],"production_by_planet":{}},
    )


def test_remote_mining_target_requests_first_race_legal_robot_unlock():
    demands=_build_demands(_state(),None)
    miner=next(demand for demand in demands if demand.capability.capability_id.startswith("component:remote_miner:"))
    assert miner.capability.name == "Robo-Mini-Miner"
    assert miner.capability.requirements == {"construction":2,"electronics":1}


def test_tiny_scout_contact_does_not_trigger_research_emergency():
    state=_state()
    state.fleets=[
        Fleet(9,"Scout group",2,Position(0,0),combat_power=78,native={"mass":78,"ship_count":[3]}),
    ]
    assert _nearby_threats(state,None) == []
