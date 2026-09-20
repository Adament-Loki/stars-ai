"""Mutable per-turn planning context for the public AI facade."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from ..memory import AgentMemory
from ..models import GameState, OrderSet
from ..persona import StrategicPersona
from .ai_state import TurnInput

if TYPE_CHECKING:
    from ..agent import StarsAgent


@dataclass(slots=True)
class TurnContext:
    """References the working objects used while planning a single turn.

    Phase 1 deliberately keeps this thin.  It is a place for later planner
    adapters to share per-turn references, not a replacement for
    ``GameState.native`` or ``AgentMemory``.
    """

    turn_input: TurnInput
    state: GameState
    memory: AgentMemory
    persona: StrategicPersona
    orders: OrderSet | None = None
    decision_events: list[dict[str, Any]] = field(default_factory=list)
    legacy_agent: "StarsAgent | None" = None
