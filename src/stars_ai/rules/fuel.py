
from __future__ import annotations

def fuel_required(
    *,
    mass_kt: float,
    distance_ly: float,
    fuel_usage_number: float,
    improved_fuel_efficiency: bool = False,
) -> float:
    """
    Based on the Stars! rule-of-thumb:
    1 mg fuel moves 200 kt by 1 LY at fuel-usage-number 100.
    """
    modifier = 0.85 if improved_fuel_efficiency else 1.0
    return (mass_kt / 200.0) * distance_ly * (fuel_usage_number / 100.0) * modifier

def max_range(
    *,
    fuel_mg: float,
    mass_kt: float,
    fuel_usage_number: float,
    improved_fuel_efficiency: bool = False,
) -> float:
    per_ly = fuel_required(
        mass_kt=mass_kt,
        distance_ly=1.0,
        fuel_usage_number=fuel_usage_number,
        improved_fuel_efficiency=improved_fuel_efficiency,
    )
    if per_ly <= 0:
        return float("inf")
    return fuel_mg / per_ly
