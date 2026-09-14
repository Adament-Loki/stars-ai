from stars_ai.models import GameState, RaceProfile, Tech, Planet, Fleet, Position
from stars_ai.objective_production import plan_objective_ship_builds
from stars_ai.fleet_intent import _mining_candidates


def _state(year=2405, ar=False, minerals=(1000,1000,1000)):
    race=RaceProfile()
    if ar:
        race.primary_trait="Alternate Reality"
        race.native={"prt_id":8}
    dp=[
        {"design_number":0,"name":"Scout","role":"scout","dry_mass":20,"fuel_capacity":200,"engine_id":3},
        {"design_number":2,"name":"Colony","role":"colony","dry_mass":60,"fuel_capacity":200,"engine_id":3},
        {"design_number":5,"name":"Cotton Picker","role":"miner","dry_mass":574,"fuel_capacity":210,"engine_id":3},
    ]
    home=Planet(0,"Home",Position(0,0),owner=1,observed=True,habitability=100,population=500000,
        ironium=minerals[0],boranium=minerals[1],germanium=minerals[2],native={
            "is_homeworld":True,"has_starbase":True,
            "starbase_capabilities":{"can_build_ships":True,"can_refuel":True},
            "mineral_concentrations":[80,80,80]})
    target=Planet(1,"Rich Rock",Position(20,0),owner=None,observed=True,habitability=-20,
        native={"mineral_concentrations":[80,80,80]})
    unknown=[Planet(i,f"U{i}",Position(i,20),owner=None,observed=False) for i in range(2,20)]
    return GameState("g",year,1,race,Tech(),[home,target,*unknown],[],native={"design_profiles":dp,"production_by_planet":{}})


def test_early_rich_target_does_not_authorize_expensive_remote_miners():
    s=_state(year=2408,minerals=(0,0,0))
    requests=plan_objective_ship_builds(s)
    assert not [r for r in requests if r.role=="miner"]
    policy=s.native["remote_mining_build_policy"]
    assert policy["mature_empire"] is False
    assert policy["real_mineral_pressure"] is False
    assert policy["desired_miners"] == 0


def test_mature_empire_with_real_reserve_deficit_can_build_remote_miners():
    s=_state(year=2435,minerals=(0,0,0))
    requests=plan_objective_ship_builds(s)
    miners=[r for r in requests if r.role=="miner"]
    assert miners
    policy=s.native["remote_mining_build_policy"]
    assert policy["mature_empire"] is True
    assert policy["real_mineral_pressure"] is True
    assert policy["desired_miners"] >= 1


def test_late_game_ar_scales_miner_demand_and_accepts_homeworld_target():
    s=_state(year=2440,ar=True,minerals=(0,0,0))
    requests=plan_objective_ship_builds(s)
    policy=s.native["remote_mining_build_policy"]
    assert policy["alternate_reality"] is True
    assert policy["real_mineral_pressure"] is True
    assert policy["desired_miners"] >= 6
    assert [r for r in requests if r.role=="miner"]
    fleet=Fleet(5,"AR Miner",1,Position(0,0),role="miner",native={"ship_count":[0,0,0,0,0,1]})
    candidates=_mining_candidates(s,fleet)
    assert candidates
    assert int(candidates[0][1].id) == 0
