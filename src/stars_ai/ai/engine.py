"""Compatibility facade for the current AI turn implementation."""

from __future__ import annotations

from ..agent import StarsAgent
from .ai_state import TurnInput
from .context import TurnContext
from .result import TurnDecision


class StarsAI:
    """Future public AI entry point backed by the unchanged legacy agent.

    Phase 1 has exactly one execution path: construct ``StarsAgent`` using
    the caller's existing state, memory, and persona references, then invoke
    ``play_turn``.  This preserves planner ordering, side effects, safety
    checks, and persistent-memory semantics while providing a stable facade
    for future architecture-only phases.
    """

    def run_turn(self, turn_input: TurnInput) -> TurnDecision:
        """Plan one turn through the existing ``StarsAgent`` implementation."""
        legacy_agent = StarsAgent(
            turn_input.state,
            memory=turn_input.memory,
            persona=turn_input.persona,
        )
        context = TurnContext(
            turn_input=turn_input,
            state=legacy_agent.state,
            memory=legacy_agent.memory,
            persona=legacy_agent.persona,
            legacy_agent=legacy_agent,
        )
        orders = legacy_agent.play_turn()
        context.orders = orders
        return TurnDecision(
            orders=orders,
            context=context,
            decision_events=context.decision_events,
        )
