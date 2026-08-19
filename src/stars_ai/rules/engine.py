
from __future__ import annotations
from dataclasses import dataclass

from .turn_order import TURN_ORDER, happens_before, can_react_same_turn
from .population import expected_population_growth, recommend_population_policy
from .fuel import fuel_required, max_range
from .gating import assess_overgate
from .minefields import recommend_minefield_warp, MinefieldType
from .packets import (
    PacketRaceClass, launch_year_distance, full_year_distance,
    packet_overhead_fraction, packet_decay, next_mass_after_decay,
)
from .salvage import battle_salvage_fraction, scrap_return, should_scrap_before_delete

@dataclass
class RulesEngine:
    """
    Deterministic/empirical Stars! mechanics facade.

    Strategy code should depend on this layer instead of embedding game formulas.
    """
    def event_before(self, a: str, b: str) -> bool:
        return happens_before(a, b)

    def population_growth(self, **kwargs):
        return expected_population_growth(**kwargs)

    def population_policy(self, *args, **kwargs):
        return recommend_population_policy(*args, **kwargs)

    def fuel_required(self, **kwargs):
        return fuel_required(**kwargs)

    def max_range(self, **kwargs):
        return max_range(**kwargs)

    def overgate(self, **kwargs):
        return assess_overgate(**kwargs)

    def minefield_warp(self, *args, **kwargs):
        return recommend_minefield_warp(*args, **kwargs)

    def packet_decay(self, **kwargs):
        return packet_decay(**kwargs)

    def scrap_return(self, **kwargs):
        return scrap_return(**kwargs)
