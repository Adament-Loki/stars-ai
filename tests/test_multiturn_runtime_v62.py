
from stars_ai.models import GameState,RaceProfile,Tech,Planet,Fleet,Position,OrderSet
from stars_ai.native.x_writer import _movement_to_planet_block,_native_waypoint_action,_colonize_blocks
from stars_ai.strategy.economy import add_economic_orders


def _state(wp_count=1, object_type=0x11):
    planets=[
        Planet(0,'Home',Position(1000,1000),owner=1,observed=True,habitability=100,population=287000),
        Planet(1,'Green',Position(1040,1000),owner=None,observed=True,habitability=80),
    ]
    fleets=[Fleet(0,'Fleet',1,Position(1000,1000),role='scout',speed=8,native={
        'waypoint_count':wp_count,
        'waypoints':[{'position_object_type':0x11}] + ([{'position_object_type':object_type}] if wp_count>=2 else []),
    })]
    return GameState('g',2400,1,RaceProfile(),Tech(),planets,fleets)


def test_first_route_uses_add():
    s=_state(1)
    b=_movement_to_planet_block(s,{'fleet_id':0,'destination_planet_id':1,'warp':8,'mission':'scan'})
    assert b.type_id==4
    assert _native_waypoint_action(s,0)=='ADD'


def test_second_route_replaces_existing_waypoint_one():
    s=_state(2,0x91)
    b=_movement_to_planet_block(s,{'fleet_id':0,'destination_planet_id':1,'warp':8,'mission':'scan'})
    assert b.type_id==5
    assert b.data[2:4]==bytes.fromhex('01 00')
    assert b.data[10]==0x80  # warp 8, task 0
    assert b.data[11]==0x91  # preserve Stars! upper target-type bits
    assert _native_waypoint_action(s,0)=='CHANGE'


def test_colony_with_realistic_home_population_emits_load_and_colonize():
    planets=[
        Planet(0,'Home',Position(0,0),owner=1,observed=True,habitability=100,population=287000),
        Planet(1,'Green',Position(40,0),owner=None,observed=True,habitability=48),
    ]
    fleets=[Fleet(2,'Colony',1,Position(0,0),role='colony',cargo_population=0,speed=8,native={'waypoint_count':1})]
    s=GameState('g',2401,1,RaceProfile(),Tech(),planets,fleets)
    o=OrderSet('g',2401,1); add_economic_orders(s,o,None)
    c=next(x for x in o.orders if x.kind=='colony_operation')
    assert c.payload['load_25k_population'] is True
    assert c.payload['source_population']==287000


def test_colony_retarget_sequence_changes_slot_then_sets_colonize_task():
    s=_state(2,0x51)
    s.fleets[0].role='colony'
    blocks=_colonize_blocks(s,{'fleet_id':0,'destination_planet_id':1,'warp':7,'load_25k_population':True})
    assert [b.type_id for b in blocks]==[1,5,5]
    assert blocks[1].data[10]==0x70
    assert blocks[2].data[10]==0x72
