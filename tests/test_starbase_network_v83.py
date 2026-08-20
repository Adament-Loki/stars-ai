from stars_ai.models import GameState, Planet, Position, RaceProfile, Tech
from stars_ai.starbase_planner import desired_support_base_count, plan_support_base_builds


def _planet(i,x,pop=140000,base=None):
    native={"strategic_value":0.8}
    if base:
        native.update({"has_starbase":True,"starbase_capabilities":base})
    return Planet(i,f"P{i}",Position(x,0),owner=1,habitability=80,population=pop,
                  factories=80,mines=50,ironium=180,boranium=120,germanium=120,native=native)


def _state(year=2425,lrts=()):
    support={"can_build_ships":True,"can_refuel":True,"name":"Space Station"}
    planets=[_planet(0,0,350000,support)]+[_planet(i,i*70) for i in range(1,9)]
    profiles=[
        {"design_number":0,"name":"Normal Station","hull_id":34,"hull_name":"Space Station","capabilities":support},
        {"design_number":1,"name":"Illegal Dock Synthetic","hull_id":33,"hull_name":"Space Dock","capabilities":support},
    ]
    return GameState("bases",year,1,RaceProfile(native={"lrts":list(lrts)}),Tech(construction=8),planets,[],native={
        "starbase_profiles":profiles,"production_by_planet":{},"design_profiles":[]
    })


def test_support_base_milestone_reaches_three_to_five_by_turn_25_30():
    assert desired_support_base_count(_state(2420))==3
    assert desired_support_base_count(_state(2425))==4
    assert desired_support_base_count(_state(2430))==5


def test_planner_can_start_multiple_high_value_support_hubs_when_behind():
    state=_state(2425)
    req=plan_support_base_builds(state)
    assert len(req)==2
    assert all(x.design_slot==0 for x in req)  # non-ISB must use Space Station, never Space Dock
    assert all("milestone" in x.reason.lower() for x in req)


def test_isb_may_prefer_existing_space_dock_design():
    state=_state(2425,lrts=("ISB",))
    req=plan_support_base_builds(state)
    assert req
    assert all(x.design_slot==1 for x in req)
