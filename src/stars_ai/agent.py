"""Compatibility wrapper for callers that still construct ``StarsAgent``."""

from __future__ import annotations

from .ai.engine import StarsAI
from .ai.ai_state import TurnInput
from .memory import AgentMemory
from .models import GameState, OrderSet
from .persona import BalancedPersona, StrategicPersona, StrategicPlan


class StarsAgent:
    """Legacy API shim delegating complete turn orchestration to ``StarsAI``.

    Constructor attributes and ``play_turn()`` retain their historical shape
    for the native writer, reports, tests, and external callers.  Strategy
    orchestration belongs solely to :meth:`StarsAI.run_turn`.
    """

    def __init__(
        self,
        state: GameState,
        memory: AgentMemory | None = None,
        persona: StrategicPersona | None = None,
    ):
        self.state = state
        self.memory = memory or AgentMemory()
        self.persona = persona or BalancedPersona()
        self.last_plan: StrategicPlan | None = None
        self.fleet_intents: list[dict] = []
        self._stars_ai = StarsAI()

    def play_turn(self) -> OrderSet:
        """Delegate to the authoritative orchestration path."""

        decision = self._stars_ai.run_turn(TurnInput(
            state=self.state,
            player_id=self.state.player_id,
            memory=self.memory,
            persona=self.persona,
        ))
        self.last_plan = decision.context.strategic_plan
        self.fleet_intents = decision.context.legacy_fleet_intents
        return decision.orders

    def _observe(self) -> None:
        """Compatibility hook retained for external callers."""

        self.memory.reconcile_state(self.state)
