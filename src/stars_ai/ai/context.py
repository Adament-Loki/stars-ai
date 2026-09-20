"""Mutable per-turn planning context for the public AI facade."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from ..memory import AgentMemory
from ..models import GameState, OrderSet
from ..persona import StrategicPersona
from .ai_state import AIState, TurnInput

if TYPE_CHECKING:
    from ..agent import StarsAgent


@dataclass(slots=True)
class TurnContext:
    """References the working objects used while planning a single turn.

    ``ai_state`` is a typed facade view of observed, persistent, and turn-only
    state.  The unchanged legacy agent remains available during this migration
    and retains its established ``GameState.native`` compatibility mirror.
    """

    turn_input: TurnInput
    state: GameState
    memory: AgentMemory
    persona: StrategicPersona
    orders: OrderSet | None = None
    decision_events: list[dict[str, Any]] = field(default_factory=list)
    legacy_agent: "StarsAgent | None" = None
    ai_state: AIState | None = None
