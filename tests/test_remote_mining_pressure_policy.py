from stars_ai.models import GameState, RaceProfile, Tech, Planet, Fleet, Position, OrderSet
from stars_ai.objective_production import plan_objective_ship_builds
from stars_ai.fleet_intent import _mining_candidates, ensure_fleet_activity


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


def test_mature_empire_waits_for_the_top_miner_package():
    s=_state(year=2435,minerals=(0,0,0))
    requests=plan_objective_ship_builds(s)
    assert not [r for r in requests if r.role=="miner"]
    policy=s.native["remote_mining_build_policy"]
    assert policy["real_mineral_pressure"] is True
    assert policy["top_tier_package_ready"] is False
    assert policy["desired_miners"] == 0

    s.tech=Tech(construction=14,electronics=6)
    requests=plan_objective_ship_builds(s)
    assert [r for r in requests if r.role=="miner"]
    assert s.native["remote_mining_build_policy"]["desired_miners"] >= 1


def test_remote_miner_rejects_a_world_far_from_home():
    s=_state(year=2408)
    s.planets=[planet for planet in s.planets if int(planet.id)!=1]
    far=Planet(30,"Bob",Position(1284,0),owner=None,observed=True,habitability=-20,
        native={"mineral_concentrations":[120,120,120]})
    s.planets.append(far)
    fleet=Fleet(5,"Miner",1,Position(0,0),role="miner")
    assert _mining_candidates(s,fleet)==[]
    fleet.destination_planet_id=30
    s.fleets=[fleet]
    orders=OrderSet("g",2408,1)
    ensure_fleet_activity(s, orders)
    cancel=next(order for order in orders.orders if order.kind=="cancel_scout_route")
    assert cancel.payload["fleet_id"]==5
    assert cancel.payload["cancelled_destination_planet_id"]==30


def test_early_ar_miner_works_its_homeworld():
    s=_state(year=2408, ar=True, minerals=(0,0,0))
    fleet=Fleet(5,"AR Miner",1,Position(0,0),role="miner")
    candidates=_mining_candidates(s,fleet)
    assert candidates
    assert int(candidates[0][1].id)==0


def test_late_game_ar_scales_miner_demand_and_accepts_homeworld_target():
    s=_state(year=2440,ar=True,minerals=(0,0,0))
    plan_objective_ship_builds(s)
    waiting=s.native["remote_mining_build_policy"]
    assert waiting["alternate_reality"] is True
    assert waiting["desired_miners"] == 0

    s.tech=Tech(construction=14,electronics=6)
    requests=plan_objective_ship_builds(s)
    policy=s.native["remote_mining_build_policy"]
    assert policy["top_tier_package_ready"] is True
    assert policy["desired_miners"] >= 6
    assert [r for r in requests if r.role=="miner"]
    fleet=Fleet(5,"AR Miner",1,Position(0,0),role="miner",native={"ship_count":[0,0,0,0,0,1]})
    candidates=_mining_candidates(s,fleet)
    assert candidates
    assert int(candidates[0][1].id) == 0
