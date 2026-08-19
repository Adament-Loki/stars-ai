
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
import math

class PacketRaceClass(str, Enum):
    PACKET_PHYSICS = "packet_physics"
    INTERSTELLAR_TRAVELLER = "interstellar_traveller"
    OTHER = "other"

@dataclass
class PacketDecay:
    yearly_fraction: float
    minimum_per_mineral_kt: float

def launch_year_distance(warp: int) -> float:
    return (warp * warp) / 2.0

def full_year_distance(warp: int) -> float:
    return float(warp * warp)

def packet_overhead_fraction(race_class: PacketRaceClass) -> float:
    if race_class == PacketRaceClass.PACKET_PHYSICS:
        return 0.0
    if race_class == PacketRaceClass.INTERSTELLAR_TRAVELLER:
        return 0.20
    return 0.10

def packet_decay(
    *,
    race_class: PacketRaceClass,
    firing_warp: int,
    driver_rating: int,
    dual_identical_drivers: bool=False,
) -> PacketDecay:
    effective_rating = driver_rating + (1 if dual_identical_drivers else 0)
    over = max(0, firing_warp - effective_rating)

    if race_class == PacketRaceClass.INTERSTELLAR_TRAVELLER:
        if over <= 0:
            frac = 0.10
        elif over == 1:
            frac = 0.25
        else:
            frac = 0.50
        return PacketDecay(frac, 10.0)

    table = {0: 0.0, 1: 0.10, 2: 0.25}
    frac = table.get(over, 0.50)
    minimum = 10.0
    if race_class == PacketRaceClass.PACKET_PHYSICS:
        frac *= 0.5
        minimum = 5.0
    return PacketDecay(frac, minimum)

def next_mass_after_decay(mass_kt: float, decay: PacketDecay) -> float:
    if decay.yearly_fraction <= 0:
        return mass_kt
    loss = max(mass_kt * decay.yearly_fraction, decay.minimum_per_mineral_kt)
    return max(0.0, mass_kt - loss)
