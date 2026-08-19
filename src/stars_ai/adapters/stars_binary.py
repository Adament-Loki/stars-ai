"""
Native Stars! file adapter boundary.

V1 deliberately leaves this disabled until the exact binary parser/writer is
validated against real Stars! test files.

The intended contract is:

    .m# + .xy -> GameState
    OrderSet  -> .x#

No strategy module should ever parse binary turn files directly.
"""

from pathlib import Path
from .base import TurnAdapter
from ..models import GameState, OrderSet


class StarsBinaryTurnAdapter(TurnAdapter):
    def read_state(self, path: Path, player_id: int) -> GameState:
        raise NotImplementedError(
            "Native Stars! .m# parsing is not enabled in V1. "
            "Use JsonTurnAdapter while validating the binary integration."
        )

    def write_orders(self, orders: OrderSet, path: Path) -> None:
        raise NotImplementedError(
            "Native Stars! .x# writing is not enabled in V1."
        )
