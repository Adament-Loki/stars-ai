"""Result contract returned by the public AI facade."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import OrderSet
from .context import TurnContext


@dataclass(slots=True)
class TurnDecision:
    """Semantic orders and the associated working context for one AI turn."""

    orders: OrderSet
    context: TurnContext
    decision_events: list[dict[str, Any]] = field(default_factory=list)
