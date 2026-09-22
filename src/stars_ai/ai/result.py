"""Result contract returned by the public AI facade."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import OrderSet
from .ai_state import AIState
from .context import TurnContext
from .decision_journal import DecisionJournal


@dataclass(slots=True)
class TurnDecision:
    """Semantic orders and the associated working context for one AI turn."""

    orders: OrderSet
    context: TurnContext
    # Compatibility surface retained for callers predating the typed journal.
    decision_events: list[dict[str, Any]] = field(default_factory=list)
    decision_journal: DecisionJournal = field(default_factory=DecisionJournal)

    @property
    def ai_state(self) -> AIState:
        """Return the typed state associated with this completed turn."""

        if self.context.ai_state is None:  # Defensive for manually built contexts.
            raise RuntimeError("TurnDecision has no associated AIState")
        return self.context.ai_state
