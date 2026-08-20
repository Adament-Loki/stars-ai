from stars_ai.colony_planner import (
    colony_planet_is_eligible,
    colonization_policy,
    score_colony_candidates,
)
from stars_ai.models import Fleet, GameState, Planet, Position, RaceProfile, Tech


def _state(year, targets, *, universal=False):
    home=Planet(
        0,"Home",Position(0,0),owner=1,observed=True,
        habitability=100,population=100_000,
    )
    fleet=Fleet(0,"Colony",1,Position(0,0),role="colony")
    race=RaceProfile(native={"universal_hab":True} if universal else {})
    return GameState("g",year,1,race,Tech(),[home,*targets],[fleet])


def _world(pid,name,hab,x=30,minerals=None,**native):
    data=dict(native)
    if minerals is not None:
        data["mineral_concentrations"]=list(minerals)
    return Planet(
        pid,name,Position(x,0),owner=None,observed=True,
        habitability=hab,native=data,
    )


def test_opening_uses_racial_60_percent_quality_floor():
    state=_state(2405,[
        _world(1,"Marginal",59,x=10,minerals=[50,50,50]),
        _world(2,"Attractive",60,x=40,minerals=[50,50,50]),
        _world(3,"Excellent",78,x=60,minerals=[50,50,50]),
    ])

    policy=colonization_policy(state)
    ranked=score_colony_candidates(state,state.fleets[0])

    assert policy.stage=="opening_quality"
    assert policy.normal_habitability_floor==60
    assert [x.planet_name for x in ranked]==["Excellent","Attractive"]
    assert all("race-adjusted hab" in x.explanation for x in ranked)


def test_opening_allows_only_compelling_resource_exception_below_60():
    state=_state(2405,[
        _world(1,"Ordinary 55",55,minerals=[65,65,65]),
        _world(2,"Rich 55",55,x=35,minerals=[95,90,90]),
        _world(3,"Rich But Too Poor",45,x=40,minerals=[100,100,100]),
    ])

    ranked=score_colony_candidates(state,state.fleets[0])

    assert [x.planet_name for x in ranked]==["Rich 55"]
    assert ranked[0].selection_basis=="exceptional_resources"


def test_midgame_broadens_to_less_habitable_worlds():
    state=_state(2430,[
        _world(1,"Forty",40,minerals=[40,40,40]),
        _world(2,"Thirty Four",34,x=35,minerals=[40,40,40]),
        _world(3,"Rich Twenty Five",25,x=40,minerals=[80,75,70]),
    ])

    policy=colonization_policy(state)
    ranked=score_colony_candidates(state,state.fleets[0])

    assert policy.stage=="midgame_expansion"
    assert policy.normal_habitability_floor==35
    assert {x.planet_name for x in ranked}=={"Forty","Rich Twenty Five"}
    assert next(x for x in ranked if x.planet_name=="Rich Twenty Five").selection_basis=="exceptional_resources"


def test_late_resource_expansion_can_prefer_rich_low_hab_world():
    state=_state(2460,[
        _world(1,"Poor Thirty",30,minerals=[10,10,10]),
        _world(2,"Rich Ten",10,x=32,minerals=[95,90,85]),
    ])

    policy=colonization_policy(state)
    ranked=score_colony_candidates(state,state.fleets[0])

    assert policy.stage=="late_resource_expansion"
    assert policy.normal_habitability_floor==1
    assert ranked[0].planet_name=="Rich Ten"


def test_colony_ship_demand_uses_same_phase_eligibility():
    low=_world(1,"Too Marginal For Opening",52,minerals=[50,50,50])
    early=_state(2405,[low])
    late=_state(2460,[low])

    assert colony_planet_is_eligible(early,low) is False
    assert colony_planet_is_eligible(late,late.planets[1]) is True


def test_universal_hab_race_remains_resource_driven():
    state=_state(2405,[
        _world(1,"Poor Rock",1,minerals=[10,10,10]),
        _world(2,"Rich Rock",1,x=40,minerals=[90,90,90]),
    ],universal=True)

    ranked=score_colony_candidates(state,state.fleets[0])

    assert colonization_policy(state).normal_habitability_floor is None
    assert ranked[0].planet_name=="Rich Rock"
    assert "habitability ignored" in ranked[0].explanation
