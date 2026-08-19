
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass
class CounterDesignSpec:
    role: str
    preferred_weapon: str
    speed_priority: float
    initiative_priority: float
    shield_priority: float
    armor_priority: float
    jammer_priority: float
    cost_priority: float
    reason: str

def generate_counter_doctrine(enemy_profiles: list[Any]) -> CounterDesignSpec:
    beam=sum(float(getattr(p,"beam_strength",0) or 0) for p in enemy_profiles)
    torp=sum(float(getattr(p,"torpedo_strength",0) or 0) for p in enemy_profiles)
    shield=sum(float(getattr(p,"shield_strength",0) or 0) for p in enemy_profiles)
    armor=sum(float(getattr(p,"armor_strength",0) or 0) for p in enemy_profiles)
    chaff=sum(1 for p in enemy_profiles if float(getattr(p,"resource_cost",999))<30 and float(getattr(p,"effective_combat_value",0))>0)
    if torp>beam and chaff>0:
        return CounterDesignSpec("FAST_BEAMER","beam",0.95,0.95,0.65,0.55,0.35,0.55,
            "Enemy is missile/chaff heavy; fast high-initiative beamers can remove chaff before missile exchanges.")
    if torp>beam:
        return CounterDesignSpec("ANTI_MISSILE","beam",0.75,0.8,0.7,0.55,0.9,0.5,
            "Enemy missile weight is high; favor jamming plus beam pressure and initiative.")
    if beam>torp:
        return CounterDesignSpec("STANDOFF_MISSILE","torpedo",0.55,0.75,0.85,0.75,0.5,0.45,
            "Enemy beam weight is high; favor ranged missile pressure and durability.")
    return CounterDesignSpec("BALANCED_CAPITAL","mixed",0.65,0.65,0.7,0.7,0.55,0.55,
        "Enemy doctrine is mixed or unclear; retain balanced fleet composition.")
