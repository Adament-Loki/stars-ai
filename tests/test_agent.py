import json
from pathlib import Path
from stars_ai.models import GameState
from stars_ai.agent import StarsAgent


def test_v1_generates_orders():
    example = Path(__file__).parents[1] / "examples" / "player2-turn2405.json"
    state = GameState.from_dict(json.loads(example.read_text(encoding="utf-8")))
    orders = StarsAgent(state).play_turn()
    kinds = [o.kind for o in orders.orders]
    assert "set_research" in kinds
    assert "move_fleet" in kinds
