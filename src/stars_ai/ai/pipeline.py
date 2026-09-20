"""Minimal planner protocol reserved for later adapter phases."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .context import TurnContext


@runtime_checkable
class TurnPlanner(Protocol):
    """A future planner stage that can mutate one shared turn context."""

    name: str

    def plan(self, context: TurnContext) -> None:
        """Apply this stage's decisions to ``context``."""
