
from stars_ai.models import GameState,RaceProfile,Tech,Planet,Fleet,Position
from stars_ai.objective_production import plan_objective_ship_builds
from stars_ai.native.x_writer import _encode_queue_item

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
