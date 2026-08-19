
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
import json


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


@dataclass
class ScoreFactor:
    name: str
    value: float
    weight: float = 1.0
    contribution: float | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.contribution is None:
            self.contribution = self.value * self.weight


@dataclass
class CandidateScore:
    candidate: str
    score: float
    factors: list[ScoreFactor] = field(default_factory=list)
    disqualified: bool = False
    disqualify_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DecisionEvent:
    category: str
    decision: str
    reason: str
    selected: str | None = None
    candidates: list[CandidateScore] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    goals: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    turn: int | None = None
    timestamp_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class DecisionTrace:
    """
    Structured, human-readable decision journal for one AI turn.

    Intended use:
        trace.record(...)
        trace.write_json(...)
        trace.write_text(...)
    """

    def __init__(
        self,
        *,
        player_id: int | None = None,
        turn: int | None = None,
        persona: str | None = None,
        enabled: bool = True,
    ) -> None:
        self.player_id = player_id
        self.turn = turn
        self.persona = persona
        self.enabled = enabled
        self.events: list[DecisionEvent] = []

    def record(
        self,
        category: str,
        decision: str,
        reason: str,
        *,
        selected: str | None = None,
        candidates: list[CandidateScore] | None = None,
        context: dict[str, Any] | None = None,
        goals: list[str] | None = None,
        rules: list[str] | None = None,
    ) -> DecisionEvent | None:
        if not self.enabled:
            return None
        event = DecisionEvent(
            category=category,
            decision=decision,
            reason=reason,
            selected=selected,
            candidates=candidates or [],
            context=context or {},
            goals=goals or [],
            rules=rules or [],
            turn=self.turn,
        )
        self.events.append(event)
        return event

    def score_candidate(
        self,
        candidate: str,
        factors: list[ScoreFactor],
        *,
        disqualified: bool = False,
        disqualify_reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CandidateScore:
        score = sum(float(f.contribution or 0.0) for f in factors)
        if disqualified:
            score = float("-inf")
        return CandidateScore(
            candidate=candidate,
            score=score,
            factors=factors,
            disqualified=disqualified,
            disqualify_reason=disqualify_reason,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return _jsonable({
            "player_id": self.player_id,
            "turn": self.turn,
            "persona": self.persona,
            "events": self.events,
        })

    def write_json(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=False), encoding="utf-8")
        return p

    def write_text(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.render_text(), encoding="utf-8")
        return p

    def render_text(self) -> str:
        lines = []
        head = f"Stars! AI Decision Trace"
        if self.player_id is not None:
            head += f" | Player {self.player_id}"
        if self.turn is not None:
            head += f" | Turn {self.turn}"
        if self.persona:
            head += f" | Persona {self.persona}"
        lines.append(head)
        lines.append("=" * len(head))
        lines.append("")

        for idx, e in enumerate(self.events, 1):
            lines.append(f"[{idx}] {e.category.upper()}: {e.decision}")
            if e.selected is not None:
                lines.append(f"Selected: {e.selected}")
            lines.append(f"Why: {e.reason}")

            if e.goals:
                lines.append("Goals:")
                for g in e.goals:
                    lines.append(f"  - {g}")

            if e.rules:
                lines.append("Rules:")
                for r in e.rules:
                    lines.append(f"  - {r}")

            if e.context:
                lines.append("Context:")
                for k, v in e.context.items():
                    lines.append(f"  {k}: {_jsonable(v)}")

            if e.candidates:
                lines.append("Candidates:")
                ordered = sorted(
                    e.candidates,
                    key=lambda c: c.score if c.score != float("-inf") else -1e100,
                    reverse=True,
                )
                for c in ordered:
                    if c.disqualified:
                        lines.append(f"  - {c.candidate}: DISQUALIFIED ({c.disqualify_reason})")
                    else:
                        lines.append(f"  - {c.candidate}: {c.score:.3f}")
                    for f in c.factors:
                        lines.append(
                            f"      {f.name}: value={f.value:.3f}, "
                            f"weight={f.weight:.3f}, contribution={float(f.contribution or 0):.3f}"
                            + (f" | {f.reason}" if f.reason else "")
                        )
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"


class NullDecisionTrace(DecisionTrace):
    def __init__(self) -> None:
        super().__init__(enabled=False)
