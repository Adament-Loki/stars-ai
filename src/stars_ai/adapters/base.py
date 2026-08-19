from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path
from ..models import GameState, OrderSet


class TurnAdapter(ABC):
    @abstractmethod
    def read_state(self, path: Path, player_id: int) -> GameState:
        raise NotImplementedError

    @abstractmethod
    def write_orders(self, orders: OrderSet, path: Path) -> None:
        raise NotImplementedError
