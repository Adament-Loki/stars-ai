
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class ScrapReturn:
    minerals_fraction: float
    resources_fraction: float

def battle_salvage_fraction() -> float:
    return 1.0 / 3.0

def scrap_return(*, at_starbase: bool, ultimate_recycling: bool) -> ScrapReturn:
    if at_starbase:
        if ultimate_recycling:
            return ScrapReturn(0.90, 0.70)
        return ScrapReturn(0.80, 0.0)
    if ultimate_recycling:
        return ScrapReturn(0.45, 0.35)
    return ScrapReturn(1.0/3.0, 0.0)

def should_scrap_before_delete(active_count: int) -> bool:
    return active_count > 0
