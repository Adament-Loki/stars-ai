
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass
class InvasionPlan:
    planet_id: int
    action: str
    required_population: int
    bomber_priority: float
    escort_priority: float
    holdability: float
    reason: str

def plan_invasion(planet: Any, *, our_local_strength: float, enemy_local_strength: float, transport_capacity: int) -> InvasionPlan:
    pop=int(getattr(planet,"population",0) or 0)
    defs=int(getattr(planet,"defenses",0) or 0)
    factories=int(getattr(planet,"factories",0) or 0)
    mines=int(getattr(planet,"mines",0) or 0)
    native=getattr(planet,"native",{}) or {}
    strategic=float(native.get("strategic_value",0.5))
    hab=float(getattr(planet,"habitability",0) or 0)/100.0
    infrastructure=min(1.0,(factories+mines)/500)
    strength_ratio=our_local_strength/max(1.0,enemy_local_strength)
    hold=min(1.0,0.4*min(1,strength_ratio/1.3)+0.35*strategic+0.25*hab)
    required=min(transport_capacity,max(100, int(pop*(1.1 + defs/100.0))))
    if hab<0.10 and strategic<0.45:
        action="BOMB_OR_BYPASS"
    elif hold>=0.60 and (hab>=0.30 or infrastructure>=0.5):
        action="CAPTURE"
    elif strategic>=0.75:
        action="NEUTRALIZE"
    else:
        action="BYPASS"
    bomber=min(1.0,0.35+defs/100.0+pop/1000000.0)
    escort=min(1.0,0.4+enemy_local_strength/max(1.0,our_local_strength+enemy_local_strength))
    return InvasionPlan(planet.id,action,required,bomber,escort,hold,
        f"Capture value balances habitability/infrastructure/strategic value against defenses, troop lift, and ability to hold the world.")
