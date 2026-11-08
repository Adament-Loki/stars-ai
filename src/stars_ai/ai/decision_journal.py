"""Structured, deterministic decision evidence for one AI planning turn.

This journal is intentionally limited to inspectable program decisions: the
planner/policy that acted, semantic action, target, priority, stated reason,
and available scored alternatives.  It is not a record of private model
reasoning.  The journal is consumed by the human Markdown and JSON reporters.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from ..decision_trace import DecisionEvent
from ..models import Order


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _planet_label(state, planet_id: Any) -> str | None:
    if planet_id is None:
        return None
    try:
        numeric_id = int(planet_id)
    except (TypeError, ValueError):
        return str(planet_id)
    planet = next((item for item in state.planets if int(item.id) == numeric_id), None)
    return planet.name if planet is not None else f"Planet {numeric_id}"


def _subject_and_target(state, order: Order) -> tuple[str | None, str | None]:
    payload = order.payload
    fleet_id = payload.get("fleet_id")
    if fleet_id is not None:
        fleet = next(
            (item for item in state.fleets if int(item.id) == int(fleet_id)), None,
        )
        subject = fleet.name if fleet is not None else f"Fleet {fleet_id}"
    elif payload.get("planet_id") is not None:
        subject = _planet_label(state, payload.get("planet_id"))
    else:
        subject = None

    target = _planet_label(state, payload.get("destination_planet_id"))
    if target is None and payload.get("target_fleet_id") is not None:
        target = f"Fleet {payload['target_fleet_id']}"
    if target is None and payload.get("design_name"):
        target = str(payload["design_name"])
    return subject, target


class DecisionJournal:
    """One append-only structured source of decision evidence for a turn.

    It is intentionally separate from the legacy lightweight
    ``TurnContext.decision_events`` list, which remains available for
    compatibility.
    """

    def __init__(
        self,
        *,
        player_id: int | None = None,
        turn: int | None = None,
        persona: str | None = None,
    ) -> None:
        self.player_id = player_id
        self.turn = turn
        self.persona = persona
        self.events: list[DecisionEvent] = []

    def __iter__(self) -> Iterator[DecisionEvent]:
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)

    def append(self, value: DecisionEvent | dict[str, Any]) -> None:
        """Accept legacy dict events while normalizing them for reporters."""

        if isinstance(value, DecisionEvent):
            self.events.append(value)
            return
        if not isinstance(value, dict):
            raise TypeError("DecisionJournal events must be DecisionEvent or dict")
        self.record(
            domain=str(value.get("domain", value.get("category", "legacy"))),
            action=str(value.get("action", value.get("decision", value.get("kind", "event")))),
            reason=str(value.get("reason", "Legacy decision event.")),
            subject=value.get("subject"),
            target=value.get("target", value.get("selected")),
            priority=value.get("priority"),
            evidence=dict(value.get("evidence", value.get("context", {})) or {}),
            alternatives=list(value.get("alternatives", value.get("candidates", [])) or []),
            warnings=list(value.get("warnings", []) or []),
            expected_outcome=value.get("expected_outcome"),
        )

    def record(
        self,
        *,
        domain: str,
        action: str,
        reason: str,
        subject: str | None = None,
        target: str | None = None,
        priority: int | None = None,
        selected: bool = True,
        evidence: dict[str, Any] | None = None,
        alternatives: list[Any] | None = None,
        warnings: list[str] | None = None,
        expected_outcome: Any | None = None,
    ) -> DecisionEvent:
        event = DecisionEvent(
            category=str(domain),
            decision=str(action),
            reason=str(reason),
            selected=target if selected else None,
            context={
                "subject": subject,
                "target": target,
                "priority": priority,
                "selected": bool(selected),
                "evidence": _jsonable(evidence or {}),
                "alternatives": _jsonable(alternatives or []),
                "warnings": [str(item) for item in (warnings or [])],
                "expected_outcome": _jsonable(expected_outcome),
            },
            turn=self.turn,
        )
        self.events.append(event)
        return event

    def record_order(self, domain: str, state, order: Order) -> DecisionEvent:
        """Record an order immediately after its planner/policy proposes it."""

        subject, target = _subject_and_target(state, order)
        return self.record(
            domain=domain,
            action=order.kind,
            reason=order.reason,
            subject=subject,
            target=target,
            priority=int(order.priority),
            evidence={"payload": _jsonable(order.payload)},
        )

    def record_fleet_intent(self, state, intent: dict[str, Any]) -> DecisionEvent:
        return self.record(
            domain="fleet_activity",
            action=str(intent.get("action", "FLEET INTENT")),
            reason=str(intent.get("reason", "No fleet-intent reason was recorded.")),
            subject=str(intent.get("fleet_name") or f"Fleet {intent.get('fleet_id', '?')}"),
            target=_planet_label(state, intent.get("destination_planet_id")),
            alternatives=list(intent.get("colony_candidates", []) or []),
            evidence={
                "fleet_id": intent.get("fleet_id"),
                "role": intent.get("role"),
            },
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-ready projection shared by both renderers."""

        events = []
        for ordinal, event in enumerate(self.events, start=1):
            context = dict(event.context or {})
            events.append({
                "ordinal": ordinal,
                "domain": event.category,
                "action": event.decision,
                "reason": event.reason,
                "subject": context.get("subject"),
                "target": context.get("target", event.selected),
                "priority": context.get("priority"),
                "selected": bool(context.get("selected", event.selected is not None)),
                "evidence": _jsonable(context.get("evidence", {})),
                "alternatives": _jsonable(context.get("alternatives", [])),
                "warnings": _jsonable(context.get("warnings", [])),
                "expected_outcome": _jsonable(context.get("expected_outcome")),
                "goals": _jsonable(event.goals),
                "rules": _jsonable(event.rules),
            })
        return {
            "player_id": self.player_id,
            "turn": self.turn,
            "persona": self.persona,
            "events": events,
        }
