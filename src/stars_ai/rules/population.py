
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class PopulationPolicy:
    breeder_hold_fraction: float
    mature_hold_fraction: float
    export_recommended: bool
    reason: str

def normalized_growth_factor(capacity_fraction: float) -> float:
    """
    Relative growth-shape helper, not a replacement for exact race growth %.

    0..25% capacity: full growth rate.
    25..100%: linearly declines toward zero.
    This matches the practical breeder guidance used by experienced Stars! play.
    """
    f = max(0.0, min(1.0, capacity_fraction))
    if f <= 0.25:
        return 1.0
    return max(0.0, 1.0 - (f - 0.25) / 0.75)

def expected_population_growth(
    population: int,
    race_growth_rate: float,
    habitability: float,
    capacity_fraction: float,
) -> int:
    pop = max(0, population)
    growth = max(0.0, race_growth_rate)
    hab = max(0.0, min(1.0, habitability))
    factor = normalized_growth_factor(capacity_fraction)
    return int(round(pop * growth * hab * factor))

def recommend_population_policy(
    capacity_fraction: float,
    *,
    good_export_world_available: bool,
    factories_underutilized: bool,
) -> PopulationPolicy:
    f = max(0.0, min(1.0, capacity_fraction))
    if good_export_world_available and not factories_underutilized:
        hold = 0.25
        return PopulationPolicy(
            breeder_hold_fraction=hold,
            mature_hold_fraction=0.50,
            export_recommended=f > hold,
            reason="Good alternate habitat exists; preserve breeder efficiency by exporting excess population.",
        )
    if factories_underutilized:
        return PopulationPolicy(
            breeder_hold_fraction=0.33,
            mature_hold_fraction=0.50,
            export_recommended=f > 0.50,
            reason="Retain more population temporarily to operate infrastructure and improve resource output.",
        )
    return PopulationPolicy(
        breeder_hold_fraction=0.33,
        mature_hold_fraction=0.50,
        export_recommended=f > 0.33,
        reason="Without a strong export destination, favor the per-planet growth optimum near one-third capacity.",
    )
