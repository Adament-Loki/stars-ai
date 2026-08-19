from __future__ import annotations
import json
from pathlib import Path
from .base import TurnAdapter
from ..models import GameState, OrderSet


class JsonTurnAdapter(TurnAdapter):
    def read_state(self, path: Path, player_id: int) -> GameState:
        data = json.loads(path.read_text(encoding="utf-8"))
        state = GameState.from_dict(data)
        if state.player_id != player_id:
            raise ValueError(
                f"State belongs to player {state.player_id}, not requested player {player_id}."
            )
        return state

    def write_orders(self, orders: OrderSet, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(orders.to_dict(), indent=2), encoding="utf-8")
