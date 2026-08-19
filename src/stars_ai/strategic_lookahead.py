
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Any

@dataclass
class StrategicOption:
    name: str
    horizon_turns: int
    immediate_value: float
    future_value: float
    territorial_loss: float
    fleet_loss: float
    tech_gain: float
    economic_gain: float
    risk: float
    notes: list[str]=field(default_factory=list)

@dataclass
class LookaheadDecision:
    selected: StrategicOption
    scores: dict[str,float]
    reason: str

def score_option(o: StrategicOption, *, risk_tolerance: float=0.5) -> float:
    risk_penalty=(1.2-risk_tolerance)*o.risk
    return (
        o.immediate_value
        + 0.90*o.future_value
        + 0.75*o.tech_gain
        + 0.65*o.economic_gain
        - 1.15*o.territorial_loss
        - 0.90*o.fleet_loss
        - risk_penalty
        - 0.03*o.horizon_turns
    )

def choose_strategy(options: list[StrategicOption], *, risk_tolerance: float=0.5) -> LookaheadDecision:
    if not options:
        raise ValueError("At least one strategic option is required")
    scores={o.name:score_option(o,risk_tolerance=risk_tolerance) for o in options}
    selected=max(options,key=lambda o:scores[o.name])
    return LookaheadDecision(selected,scores,
        f"Selected {selected.name} because it has the highest discounted multi-turn value after territorial, fleet, time, and risk costs.")
