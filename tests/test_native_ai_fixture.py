import os
from pathlib import Path
import pytest
from stars_ai.adapters.stars_native import inspect_m_file, decode_x_orders

FIXTURE_ROOT = Path(os.environ.get('STARS_NATIVE_FIXTURE_ROOT','/mnt/data'))
pytestmark = pytest.mark.skipif(
    not all((FIXTURE_ROOT/name).exists() for name in ('AI.m1','AI.x1','AI.xy')),
    reason='external controlled Stars! AI fixture is unavailable',
)

def test_real_ai_m1_fixture():
    state = inspect_m_file(FIXTURE_ROOT/'AI.m1', FIXTURE_ROOT/'AI.xy')
    assert state['header']['year'] == 2400
    assert state['header']['player_number'] == 1
    assert state['xy']['planet_count'] == 128
    assert state['players'][0]['planet_count'] == 1
    assert state['players'][0]['fleet_count'] == 6
    assert state['planets'][0]['name'] == 'Magellan'
    assert state['planets'][0]['surface']['population'] == 25000
    assert state['planets'][0]['installations']['factories'] == 10
    assert len(state['fleets']) == 6


def test_real_ai_x1_fixture():
    result = decode_x_orders(FIXTURE_ROOT/'AI.x1', FIXTURE_ROOT/'AI.xy')
    moves = [o for o in result['orders'] if o['type'] == 'WaypointAdd']
    assert [(m['fleet_display_id'],m['target_name'],m['warp']) for m in moves] == [
        (1,'Serapa',7),(2,'Quiche',6),(3,'Knob',9)
    ]
    task = next(o for o in result['orders'] if o['type']=='WaypointChangeTask')
    assert task['fleet_display_id'] == 6
    assert task['target_name'] == 'Magellan'
    assert task['task'] == 3  # Confirmed by user's known Remote Mining order in this fixture.
    queue = next(o for o in result['orders'] if o['type']=='ProductionQueueChange')
    assert [(i['item'],i['count']) for i in queue['items']] == [('Mine',5),('Factory',2)]
