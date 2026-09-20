"""Input contract for one public AI planning call."""

from __future__ import annotations

from dataclasses import dataclass

from ..memory import AgentMemory
from ..models import GameState
from ..persona import StrategicPersona


@dataclass(slots=True)
class TurnInput:
    """Information required to plan one player's normalized game turn.

    ``state`` and ``memory`` deliberately remain the existing project types.
    The facade does not copy, translate, or reinterpret them: that keeps the
    current mutation and persistence semantics identical to ``StarsAgent``.
    ``player_id`` is an explicit public identity field, although Phase 1 does
    not add validation beyond the legacy agent's existing behavior.
    """

    state: GameState
    player_id: int
    memory: AgentMemory | None = None
    persona: StrategicPersona | None = None
