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


def test_key_world_is_a_defensive_hub_after_the_relay_quota_is_met():
    state = _state(2430)
    support = {"can_build_ships": True, "can_refuel": True, "name": "Space Station"}
    for planet in state.planets:
        if 0 < int(planet.id) < 6:
            planet.native.update({"has_starbase": True, "starbase_capabilities": support})
    state.planets[-1].population = 300_000
    state.native["starbase_profiles"].append({
        "design_number": 2, "name": "Armed Station", "hull_id": 34,
        "hull_name": "Space Station", "capabilities": support,
        "slots": [{"category": 32, "item_id": 1, "count": 8}],
    })

    builds = plan_support_base_builds(state)
    hub = next(build for build in builds if build.planet_id == int(state.planets[-1].id))

    assert hub.design_slot == 2
    assert hub.priority >= 205
    assert "defensive hub" in hub.reason


def test_every_critical_world_starts_a_torpedo_station_once_that_design_exists():
    state = _state(2445)
    state.tech.weapons = 10
    support = {"can_build_ships": True, "can_refuel": True, "name": "Space Station"}
    state.native["starbase_profiles"].extend([
        {
            "design_number": 2, "name": "Laser Starter", "hull_id": 34,
            "hull_name": "Space Station", "capabilities": support,
            "slots": [{"category": 16, "item_id": 0, "count": 8}],
        },
        {
            "design_number": 4, "name": "Alpha Station", "hull_id": 34,
            "hull_name": "Space Station", "capabilities": support,
            "slots": [{"category": 32, "item_id": 0, "count": 8}],
        },
        {
            "design_number": 3, "name": "Torpedo Station", "hull_id": 34,
            "hull_name": "Space Station", "capabilities": support,
            "slots": [{"category": 32, "item_id": 2, "count": 8}],
        },
    ])
    builds = plan_support_base_builds(state)
    missing = [planet for planet in state.planets if int(planet.id) != 0]

    assert len(builds) == len(missing)
    assert {build.planet_id for build in builds} == {int(planet.id) for planet in missing}
    assert all(build.design_slot == 3 for build in builds)
    assert all(build.priority >= 220 for build in builds)
    assert all("patience" in build.reason for build in builds)


def test_a_small_world_takes_the_armed_fort_instead_of_a_late_station():
    state = _state(2445)
    state.tech.weapons = 10
    state.tech.propulsion = 2
    support = {"can_build_ships": True, "can_refuel": True, "name": "Starbase"}
    state.planets.append(Planet(
        20, "Outpost", Position(400, 0), owner=1, habitability=70, population=30_000,
        factories=10, mines=10, ironium=40, boranium=20, germanium=20,
        native={"mineral_concentrations": [30, 30, 30]},
    ))
    state.native["starbase_profiles"] = [
        {
            "design_number": 1, "name": "Fort Now", "hull_id": 32, "hull_name": "Orbital Fort",
            "capabilities": support,
            "slots": [{"category": 32, "item_id": 2, "count": 4}],
        },
        {
            "design_number": 2, "name": "Station Later", "hull_id": 34, "hull_name": "Space Station",
            "capabilities": support,
            "slots": [{"category": 32, "item_id": 2, "count": 16} for _ in range(4)],
        },
    ]
    builds = plan_support_base_builds(state)
    outpost = next(build for build in builds if build.planet_id == 20)
    assert outpost.design_slot == 1
    assert "soonest" in outpost.reason or "Orbital Fort" in outpost.reason


def test_isb_may_prefer_existing_space_dock_design():
    state=_state(2425,lrts=("ISB",))
    req=plan_support_base_builds(state)
    assert req
    assert all(x.design_slot==1 for x in req)
