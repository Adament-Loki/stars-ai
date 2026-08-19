
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class OvergateAssessment:
    range_damage_percent: float
    mass_damage_percent: float
    total_damage_percent: float
    legal_within_5x: bool
    disappearance_risk_proxy: float

def range_overgate_damage(distance: float, max_range: float) -> float:
    if max_range <= 0:
        return 100.0
    if distance <= max_range:
        return 0.0
    return max(0.0, min(99.999, 100.0 * (distance - max_range) / (4.0 * max_range)))

def mass_overgate_damage(mass: float, sending_mass_limit: float, receiving_mass_limit: float) -> float:
    if sending_mass_limit <= 0 or receiving_mass_limit <= 0:
        return 100.0
    if mass <= min(sending_mass_limit, receiving_mass_limit):
        return 0.0

    s = (5.0 * sending_mass_limit - mass) / (4.0 * sending_mass_limit)
    r = (5.0 * receiving_mass_limit - mass) / (4.0 * receiving_mass_limit)
    damage = 100.0 * (1.0 - s * r)
    return max(0.0, min(99.999, damage))

def combined_overgate_damage(mass_damage: float, range_damage: float) -> float:
    return mass_damage + (100.0 - mass_damage) * (range_damage / 100.0)

def disappearance_risk_proxy(mass: float, sending_mass_limit: float, interstellar_traveller: bool=False) -> float:
    """
    Approximation from historical empirical guidance.
    Used for expected-value strategy, not claimed exact.
    """
    if mass <= sending_mass_limit:
        return 0.0
    if mass > 5.0 * sending_mass_limit or sending_mass_limit <= 0:
        return 1.0
    a = 0.68
    survive_proxy = ((1.0-a) * ((5.0*sending_mass_limit-mass)/(4.0*sending_mass_limit))**2 + a)
    vanish = 1.0 - survive_proxy
    if interstellar_traveller:
        vanish *= 0.67
    return max(0.0, min(1.0, vanish))

def assess_overgate(
    *,
    mass: float,
    distance: float,
    sending_mass_limit: float,
    receiving_mass_limit: float,
    sending_range_limit: float,
    interstellar_traveller: bool=False,
) -> OvergateAssessment:
    legal = (
        mass <= 5.0 * sending_mass_limit
        and mass <= 5.0 * receiving_mass_limit
        and distance <= 5.0 * sending_range_limit
    )
    rd = range_overgate_damage(distance, sending_range_limit)
    md = mass_overgate_damage(mass, sending_mass_limit, receiving_mass_limit)
    td = combined_overgate_damage(md, rd)
    return OvergateAssessment(
        range_damage_percent=rd,
        mass_damage_percent=md,
        total_damage_percent=td,
        legal_within_5x=legal,
        disappearance_risk_proxy=disappearance_risk_proxy(
            mass, sending_mass_limit, interstellar_traveller
        ),
    )
