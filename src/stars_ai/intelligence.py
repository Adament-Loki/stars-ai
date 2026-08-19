
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import math

@dataclass
class IntelligenceEstimate:
    entity_id: str
    last_seen_turn: int
    age: int
    confidence: float
    observed_value: float
    estimated_low: float
    estimated_high: float
    reason: str

def estimate_stale_value(entity_id: str, observed_value: float, last_seen_turn: int, current_turn: int, annual_growth_uncertainty: float=0.10) -> IntelligenceEstimate:
    age=max(0,current_turn-last_seen_turn)
    confidence=max(0.10, math.exp(-age/12.0))
    spread=(1+annual_growth_uncertainty)**age
    low=max(0.0,observed_value/spread)
    high=observed_value*spread
    return IntelligenceEstimate(entity_id,last_seen_turn,age,confidence,observed_value,low,high,
        f"Observation is {age} turns old; widen strength/tech interval instead of treating stale intelligence as exact.")

def conservative_enemy_strength(estimates: list[IntelligenceEstimate]) -> float:
    if not estimates: return 0.0
    return sum(e.estimated_high*(1.0-0.25*e.confidence) + e.observed_value*0.25*e.confidence for e in estimates)


def penetrating_scanner_probability(
    *,
    known_penetrating_scanner: bool = False,
    last_scanner_observation_turn: int | None = None,
    current_turn: int | None = None,
    enemy_electronics_advantage: float = 0.0,
) -> float:
    """Estimate the chance planetary cover does not hide a fleet."""
    if known_penetrating_scanner:
        return 0.98
    p = 0.05 + max(0.0, min(1.0, enemy_electronics_advantage)) * 0.45
    if last_scanner_observation_turn is not None and current_turn is not None:
        age=max(0,current_turn-last_scanner_observation_turn)
        # Stale absence of penetrating scanners becomes less reassuring.
        p += min(0.35, age*0.02)
    return max(0.0,min(0.95,p))
