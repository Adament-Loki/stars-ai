
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

class MinefieldType(str, Enum):
    STANDARD = "standard"
    HEAVY = "heavy"
    SPEED_TRAP = "speed_trap"

SAFE_WARP = {
    MinefieldType.STANDARD: 4,
    MinefieldType.HEAVY: 6,
    MinefieldType.SPEED_TRAP: 5,
}

@dataclass
class MineTransitRecommendation:
    warp: int
    rationale: str
    expendable_screen_recommended: bool

def recommend_minefield_warp(
    mine_type: MinefieldType,
    *,
    distance_through_field: float,
    fleet_value: float,
    urgency: float,
    expendable_fleets: int = 0,
) -> MineTransitRecommendation:
    urgency = max(0.0, min(1.0, urgency))
    fleet_value = max(0.0, min(1.0, fleet_value))

    if mine_type == MinefieldType.SPEED_TRAP:
        warp = 5 if fleet_value >= 0.4 else (6 if urgency > 0.8 else 5)
    elif mine_type == MinefieldType.HEAVY:
        if expendable_fleets >= 10 and urgency > 0.7:
            warp = 9
        elif fleet_value > 0.7:
            warp = 6
        else:
            warp = 7
    else:
        if distance_through_field <= 64:
            warp = min(10, max(4, int(distance_through_field ** 0.5 + 0.999)))
        elif expendable_fleets >= 2 and urgency > 0.6:
            warp = 10
        else:
            warp = 9

    screen = expendable_fleets > 0 and fleet_value > 0.65 and urgency > 0.65
    return MineTransitRecommendation(
        warp=warp,
        rationale="Warp chosen from mine type, trip length, mission urgency, fleet value, and expendable-screen availability.",
        expendable_screen_recommended=screen,
    )
