from pathlib import Path
from stars_ai.adapters.native_core_adapter import NativeCoreTurnAdapter
from stars_ai.agent import StarsAgent

DATA = Path('/mnt/data')


def test_native_bridge_ai_fixture():
    state = NativeCoreTurnAdapter(DATA/'AI(2).xy', DATA/'AI(2).x1').read_state(DATA/'AI(2).m1', 1)
    assert state.player_id == 1
    assert state.tech.energy == 3
    assert state.race.primary_trait == 'Jack of All Trades'
    assert len(state.planets) > 1  # includes map-only unknown worlds from .xy
    assert any(not p.observed for p in state.planets)
    roles = {f.id: f.role for f in state.fleets if f.owner == 1}
    assert roles[0] == 'scout'
    assert roles[1] == 'scout'
    assert roles[2] == 'colony'
    assert roles[3] == 'freighter'
    assert roles[4] == 'combat'
    assert roles[5] == 'miner'
    assert state.native['designs'][0]['name'] == 'Armed Probe'


def test_native_state_runs_agent():
    state = NativeCoreTurnAdapter(DATA/'AI(2).xy', DATA/'AI(2).x1').read_state(DATA/'AI(2).m1', 1)
    orders = StarsAgent(state).play_turn()
    kinds = [o.kind for o in orders.orders]
    assert 'set_research' in kinds
    assert 'move_fleet' in kinds  # scouts can now target .xy-only unknown planets
