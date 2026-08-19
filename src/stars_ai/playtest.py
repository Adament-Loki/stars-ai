
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable
import json
from pathlib import Path

@dataclass
class PlaytestPlayer:
    player_id: int
    label: str
    persona: str
    prt: str
    human: bool = False

@dataclass
class PlaytestConfig:
    game_name: str = "v42-first-playtest"
    max_turns: int = 50
    checkpoints: list[int] = field(default_factory=lambda: [10, 25, 50])
    seed: int = 1
    players: list[PlaytestPlayer] = field(default_factory=lambda: [
        PlaytestPlayer(1, "Balanced JOAT", "Balanced", "JOAT"),
        PlaytestPlayer(2, "Expansionist JOAT", "Expansionist", "JOAT"),
        PlaytestPlayer(3, "Super Stealth", "Balanced", "SS"),
        PlaytestPlayer(4, "Space Demolition", "Balanced", "SD"),
    ])

@dataclass
class IntentRecord:
    turn: int
    player_id: int
    category: str
    target: str
    intended_action: str
    native_status: str
    rationale: str

@dataclass
class ActualRecord:
    turn: int
    player_id: int
    category: str
    target: str
    observed_result: str

@dataclass
class IntentActualMismatch:
    turn: int
    player_id: int
    category: str
    target: str
    intended_action: str
    observed_result: str
    severity: str
    reason: str

@dataclass
class CheckpointMetrics:
    turn: int
    player_id: int
    planets: int
    population: int
    factories: int
    mines: int
    fleets: int
    ships: int
    tech_sum: int
    starbases: int
    gates: int
    design_slots_used: int
    notes: list[str] = field(default_factory=list)

def metrics_from_state(state: Any, turn: int | None = None) -> CheckpointMetrics:
    owned = [p for p in state.planets if p.owner == state.player_id]
    own_fleets = [f for f in state.fleets if f.owner == state.player_id]
    ships = 0
    design_slots = set()
    for f in own_fleets:
        native = getattr(f, "native", {}) or {}
        ships += int(native.get("ship_count", native.get("ships", 1 if getattr(f, "role", None) else 0)) or 0)
        for slot in native.get("design_slots", []) or []:
            design_slots.add(slot)

    starbases = sum(1 for p in owned if (getattr(p,"native",{}) or {}).get("starbase"))
    gates = sum(1 for p in owned if (getattr(p,"native",{}) or {}).get("gate"))
    tech = getattr(state, "tech", None)
    tech_sum = 0
    if tech is not None:
        for field_name in ("energy","weapons","propulsion","construction","electronics","biotechnology"):
            tech_sum += int(getattr(tech, field_name, 0) or 0)

    return CheckpointMetrics(
        turn=int(turn if turn is not None else getattr(state, "year", 0)),
        player_id=state.player_id,
        planets=len(owned),
        population=sum(int(getattr(p,"population",0) or 0) for p in owned),
        factories=sum(int(getattr(p,"factories",0) or 0) for p in owned),
        mines=sum(int(getattr(p,"mines",0) or 0) for p in owned),
        fleets=len(own_fleets),
        ships=ships,
        tech_sum=tech_sum,
        starbases=starbases,
        gates=gates,
        design_slots_used=len(design_slots),
    )

def compare_intent_to_actual(intent: IntentRecord, actual: ActualRecord) -> IntentActualMismatch | None:
    if intent.turn != actual.turn or intent.player_id != actual.player_id:
        return None
    if intent.category != actual.category or intent.target != actual.target:
        return None

    intended = intent.intended_action.lower()
    observed = actual.observed_result.lower()

    success_tokens = ("moved","built","changed","completed","arrived","colonized","stole","detonated","laid","refueled")
    if any(tok in intended and tok in observed for tok in success_tokens):
        return None

    severity = "HIGH" if intent.native_status == "VALIDATED" else "MEDIUM"
    return IntentActualMismatch(
        turn=intent.turn,
        player_id=intent.player_id,
        category=intent.category,
        target=intent.target,
        intended_action=intent.intended_action,
        observed_result=actual.observed_result,
        severity=severity,
        reason=(
            "Validated action did not produce the expected observable state."
            if severity == "HIGH"
            else "Planned/partial action differs from observed result; determine whether native support or strategic logic is responsible."
        ),
    )

def save_checkpoint_report(
    path: str | Path,
    *,
    turn: int,
    metrics: list[CheckpointMetrics],
    intents: list[IntentRecord] | None = None,
    mismatches: list[IntentActualMismatch] | None = None,
    observer_notes: list[str] | None = None,
) -> None:
    payload = {
        "turn": turn,
        "metrics": [asdict(x) for x in metrics],
        "intents": [asdict(x) for x in (intents or [])],
        "mismatches": [asdict(x) for x in (mismatches or [])],
        "observer_notes": observer_notes or [],
    }
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")

def default_playtest_config() -> PlaytestConfig:
    return PlaytestConfig()
