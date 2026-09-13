
from stars_ai.models import GameState,RaceProfile,Tech,Planet,Fleet,Position
from stars_ai.objective_production import _selected_role_gap, plan_objective_ship_builds
from stars_ai.native.x_writer import _encode_queue_item
from stars_ai.persona import BalancedPersona

def state():
    dp=[{'design_number':0,'name':'Armed Probe','role':'scout','dry_mass':23,'fuel_capacity':50,'engine_id':3,'ram_scoop':False},{'design_number':1,'name':'Long Range Scout','role':'scout','dry_mass':25,'fuel_capacity':300,'engine_id':3,'ram_scoop':False},{'design_number':2,'name':'Santa Maria','role':'colony','dry_mass':61,'fuel_capacity':200,'engine_id':3,'ram_scoop':False},{'design_number':5,'name':'Cotton Picker','role':'miner','dry_mass':574,'fuel_capacity':210,'engine_id':3,'ram_scoop':False}]
    ps=[Planet(0,'Home',Position(0,0),owner=1,observed=True,habitability=100,population=300000,native={'has_starbase':True}),Planet(1,'A',Position(30,0),owner=None,observed=True,habitability=70),Planet(2,'B',Position(40,0),owner=None,observed=True,habitability=60)]
    ps += [Planet(i,f'U{i}',Position(i,10),owner=None,observed=False) for i in range(3,403)]
    fs=[Fleet(0,'Probe',1,Position(0,0),role='scout',native={'ship_count':[1,0,0,0,0,0]}),Fleet(1,'LR',1,Position(0,0),role='scout',native={'ship_count':[0,1,0,0,0,0]}),Fleet(2,'Colony',1,Position(0,0),role='colony',native={'ship_count':[0,0,1,0,0,0]}),Fleet(5,'Miner',1,Position(0,0),role='miner',native={'ship_count':[0,0,0,0,0,1]})]
    return GameState('g',2401,1,RaceProfile(),Tech(),ps,fs,native={'design_profiles':dp,'production_by_planet':{}})

def test_custom_ship_queue_empirical_bytes():
    assert _encode_queue_item('ship_design',1,0)==bytes.fromhex('01 00 04 00')
    assert _encode_queue_item('ship_design',1,1)==bytes.fromhex('01 04 04 00')

def test_colony_gap_builds_colony():
    r=plan_objective_ship_builds(state()); c=next(x for x in r if x.role=='colony'); assert c.design_slot==2 and c.quantity==1

def test_scout_gap_builds_long_range_design():
    r=plan_objective_ship_builds(state()); s=next(x for x in r if x.role=='scout'); assert s.design_slot==1 and s.quantity>=1


def test_low_weapons_contact_builds_only_a_two_ship_escort_screen():
    s=state()
    s.tech.weapons=3
    s.planets.append(Planet(999,"Enemy",Position(100,0),owner=2,observed=True,habitability=70))
    s.fleets.append(Fleet(99,"Enemy Raider",2,Position(105,0),role="combat",combat_power=20))
    s.native["design_profiles"].append({
        'design_number':6,'name':'Early Escort','role':'combat','dry_mass':40,
        'fuel_capacity':80,'engine_id':3,'ram_scoop':False,
    })

    combat=[request for request in plan_objective_ship_builds(s) if request.role=='combat']

    assert combat
    assert combat[0].quantity<=2
    assert "Low-tech escort doctrine" in combat[0].reason


def test_fortification_phase_builds_a_real_defensive_force_even_at_low_tech():
    s=state()
    s.tech.weapons=3
    for planet in s.planets:
        planet.observed=True
    s.native['design_profiles'].append({
        'design_number':6,'name':'Early Escort','role':'combat','dry_mass':40,
        'fuel_capacity':80,'engine_id':3,'ram_scoop':False,
    })
    plan=BalancedPersona().build_plan(s)

    combat=[request for request in plan_objective_ship_builds(s,plan) if request.role=='combat']

    assert plan.phase=='fortification'
    assert combat and combat[0].quantity >= 4
    assert 'phase=fortification requires a standing combat force' in combat[0].reason


def test_weapon_generation_change_does_not_duplicate_a_full_existing_combat_force():
    designs={
        6:{"design_number":6,"role":"combat","turn_designed":10},
        7:{"design_number":7,"role":"combat","turn_designed":20},
    }

    seed_gap,seed_reason=_selected_role_gap(
        designs,{6:24,7:0},"combat",designs[7],desired=24,role_assets=24,
    )
    settled_gap,settled_reason=_selected_role_gap(
        designs,{6:24,7:2},"combat",designs[7],desired=24,role_assets=26,
    )

    assert seed_gap == 2
    assert settled_gap == 0
    assert "proving seed=2" in seed_reason
    assert "modernization throttle" in settled_reason


def test_idle_empty_freighter_stops_new_freighter_production():
    s=state()
    s.native["design_profiles"].append({
        "design_number":3,"name":"Swashbuckler","role":"freighter",
        "dry_mass":80,"fuel_capacity":600,"cargo_capacity":250,"engine_id":3,"ram_scoop":False,
    })
    s.planets[1].owner=1
    s.planets[1].population=40_000
    s.fleets.append(Fleet(
        3,"Idle freight",1,Position(0,0),role="freighter",cargo_capacity=250,
        native={"ship_count":[0,0,0,1],"position_object_id":0,"cargo_capacity":250,
                "cargo":{"ironium":0,"boranium":0,"germanium":0,"population":0}},
    ))

    requests=plan_objective_ship_builds(s)

    assert not [request for request in requests if request.role=="freighter"]
    assert s.native["freighter_build_policy"]["idle_empty_freighter_ids"] == [3]
    assert s.native["freighter_build_policy"]["build_authorized"] is False
