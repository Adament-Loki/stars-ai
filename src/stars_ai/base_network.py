
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from .util import distance
from .colony_planner import colony_planet_is_eligible

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
    operational=[
        p for p in owned
        if bool((((p.native or {}).get("starbase_capabilities") or {}).get("can_refuel")))
    ]
    out=[]
    for p in owned:
        native=p.native or {}
        has_base=bool(native.get("starbase", native.get("has_starbase",False)))
        caps=native.get("starbase_capabilities") or {}
        can_refuel=bool(caps.get("can_refuel"))
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
        nearest_refuel=min(
            (distance(p.position,q.position) for q in operational if q.id!=p.id),
            default=0.0 if can_refuel else 999.0,
        )
        viable_frontier=sum(
            1 for q in state.planets
            if q.owner is None and q.observed
            and colony_planet_is_eligible(state,q)
            and distance(p.position,q.position)<=160.0
        )
        unknown_frontier=sum(
            1 for q in state.planets
            if not q.observed and distance(p.position,q.position)<=120.0
        )
        network_gap=(not can_refuel) and (not operational or nearest_refuel>=120.0)
        if not can_refuel:
            score+=(0.35 if network_gap else 0.0)+min(.24,.08*viable_frontier)+min(.16,.02*unknown_frontier)

        if not can_refuel and (network_gap or viable_frontier or unknown_frontier>=3):
            role="FUEL_HUB"
        elif defense>=0.72:
            role="FORTRESS"
        elif gate>=0.65:
            role="GATE_HUB"
        elif industry>=0.7:
            role="SHIPYARD"
        elif exposure>=0.5:
            role="FRONTIER_BASE"
        else:
            role="ECONOMIC_BASE"
        build_or_upgrade=(
            not can_refuel
            and score>=0.45
            and (has_base or p.population>=100000)
            and (network_gap or viable_frontier>0 or unknown_frontier>=3)
        )
        out.append(BaseRecommendation(
            p.id,role,score,build_or_upgrade,gate,defense,
            f"{p.name}: role={role}; nearest operational refuel hub="
            f"{nearest_refuel:.1f} ly; nearby viable/unknown frontier="
            f"{viable_frontier}/{unknown_frontier}; population, industry, connectivity, "
            "exposure, and refuel coverage determine base value."
        ))
    return sorted(out,key=lambda x:x.priority,reverse=True)
