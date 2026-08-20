
from stars_ai.models import GameState,RaceProfile,Tech,Planet,Fleet,Position,OrderSet
import pytest

from stars_ai.native.x_writer import (
    UnsafeWaypointMutationError,
    _movement_to_planet_block,
    _native_waypoint_action,
    _colonize_blocks,
)
from stars_ai.strategy.economy import add_economic_orders


def _state(wp_count=1, object_type=0x11, destination=1, task=0, warp=8):
    planets=[
        Planet(0,'Home',Position(1000,1000),owner=1,observed=True,habitability=100,population=287000),
        Planet(1,'Green',Position(1040,1000),owner=None,observed=True,habitability=80),
    ]
    fleets=[Fleet(0,'Fleet',1,Position(1000,1000),role='scout',speed=8,native={
        'waypoint_count':wp_count,
        'waypoints':[{'position_object_type':0x11}] + ([{
            'position_object_type':object_type,
            'position_object':destination,
            'task':task,
            'warp':warp,
        }] if wp_count>=2 else []),
    })]
    return GameState('g',2400,1,RaceProfile(),Tech(),planets,fleets)


def test_first_route_uses_add():
    s=_state(1)
    b=_movement_to_planet_block(s,{'fleet_id':0,'destination_planet_id':1,'warp':8,'mission':'scan'})
    assert b.type_id==4
    assert _native_waypoint_action(s,0)=='ADD'


def test_identical_existing_route_emits_no_replacement_order():
    s=_state(2,0x91)
    b=_movement_to_planet_block(s,{'fleet_id':0,'destination_planet_id':1,'warp':8,'mission':'scan'})
    assert b is None
    assert _native_waypoint_action(
        s,0,{'fleet_id':0,'destination_planet_id':1,'warp':8,'mission':'scan'}
    )=='CONTINUE'


def test_retarget_is_blocked_instead_of_using_change():
    s=_state(2,0x91,destination=0)
    with pytest.raises(UnsafeWaypointMutationError) as exc:
        _movement_to_planet_block(
            s,{'fleet_id':0,'destination_planet_id':1,'warp':8,'mission':'scan'}
        )
    assert exc.value.diagnostic['result']=='BLOCKED RETARGET'
    assert exc.value.diagnostic['native_waypoint_destination']==0


def test_colony_with_realistic_home_population_emits_load_and_colonize():
    planets=[
        Planet(0,'Home',Position(0,0),owner=1,observed=True,habitability=100,population=287000),
        Planet(1,'Green',Position(40,0),owner=None,observed=True,habitability=68),
    ]
    fleets=[Fleet(2,'Colony',1,Position(0,0),role='colony',cargo_population=0,speed=8,native={'waypoint_count':1})]
    s=GameState('g',2401,1,RaceProfile(),Tech(),planets,fleets)
    o=OrderSet('g',2401,1); add_economic_orders(s,o,None)
    c=next(x for x in o.orders if x.kind=='colony_operation')
    assert c.payload['load_25kt_population'] is True
    assert c.payload['source_population']==287000


def test_colony_retarget_is_blocked_before_load_or_change_blocks():
    s=_state(2,0x51,destination=0,task=2,warp=7)
    s.fleets[0].role='colony'
    with pytest.raises(UnsafeWaypointMutationError) as exc:
        _colonize_blocks(
            s,{'fleet_id':0,'destination_planet_id':1,'warp':7,'load_25kt_population':True}
        )
    assert exc.value.diagnostic['result']=='BLOCKED RETARGET'
