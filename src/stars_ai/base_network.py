
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from .util import distance

@dataclass
class BaseRecommendation:
    planet_id: int
    role: str
    priority: float
    build_or_upgrade: bool
    gate_value: float
    defense_value: float
    reason: str

def evaluate_base_network(state: Any) -> list[BaseRecommendation]:
    owned=[p for p in state.planets if p.owner==state.player_id]
    enemy_planets=[p for p in state.planets if p.owner not in (None,state.player_id)]
    out=[]
    for p in owned:
        native=p.native or {}
        has_base=bool(native.get("starbase", native.get("has_starbase",False)))
        pop=min(1.0,p.population/600000)
        industry=min(1.0,(p.factories+p.mines)/500)
        if enemy_planets:
            nearest=min(distance(p.position,e.position) for e in enemy_planets)
            exposure=max(0.0,1.0-nearest/300.0)
        else:
            exposure=0.1
        # Connectivity: number of owned worlds within useful gate radius proxy.
        neighbors=sum(1 for q in owned if q.id!=p.id and distance(p.position,q.position)<=300)
        gate=min(1.0,neighbors/5.0)
        strategic=float(native.get("strategic_value",0.5))
        defense=0.45*exposure+0.30*pop+0.25*strategic
        score=0.30*pop+0.25*industry+0.25*gate+0.20*defense
        if defense>=0.72:
            role="FORTRESS"
        elif gate>=0.65:
            role="GATE_HUB"
        elif industry>=0.7:
            role="SHIPYARD"
        elif exposure>=0.5:
            role="FRONTIER_BASE"
        else:
            role="ECONOMIC_BASE"
        out.append(BaseRecommendation(p.id,role,score,(not has_base and score>=0.58),gate,defense,
             f"{p.name}: role={role}; population/industry/connectivity/exposure determine base value."))
    return sorted(out,key=lambda x:x.priority,reverse=True)
