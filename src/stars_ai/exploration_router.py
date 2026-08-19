
from __future__ import annotations

from dataclasses import dataclass, asdict
import math
from typing import Any

from .util import distance
from .fuel_planner import (
    scout_one_way_warp,
    scout_one_way_reachable,
    profile_after_scout_leg,
    highest_zero_fuel_warp,
    fastest_fuel_safe_warp,
)


@dataclass
class ProbeRoute:
    planet_ids:list[int]
    expected_discoveries:int
    total_distance:float
    estimated_turns:float
    sector_index:int
    sector_count:int
    terminal:bool=True
    free_cruise_warp:int=0

    def to_dict(self):
        return asdict(self)


def _angle(center, point) -> float:
    return math.atan2(float(point.y)-float(center.y), float(point.x)-float(center.x))


def _angle_delta(a:float,b:float)->float:
    return abs((a-b+math.pi)%(2*math.pi)-math.pi)


def _empire_center(state):
    owned=[p for p in state.planets if p.owner==state.player_id]
    pts=owned or list(state.planets)
    if not pts:
        from .models import Position
        return Position(0.0,0.0)
    from .models import Position
    return Position(
        sum(float(p.position.x) for p in pts)/len(pts),
        sum(float(p.position.y) for p in pts)/len(pts),
    )


def scout_sector(state,fleet)->tuple[int,int,float]:
    scouts=sorted(
        [
            f for f in state.fleets
            if f.owner==state.player_id and f.role in ("scout","unknown")
        ],
        key=lambda f:int(f.id),
    )
    if not scouts:
        return (0,1,0.0)
    idx=next((i for i,f in enumerate(scouts) if int(f.id)==int(fleet.id)),0)
    count=max(1,len(scouts))
    angle=2.0*math.pi*idx/count
    return idx,count,angle


def _cluster_count(candidate, candidates, radius:float=65.0)->int:
    return sum(
        1 for p in candidates
        if p.id!=candidate.id and distance(candidate.position,p.position)<=radius
    )


def build_probe_route(
    state,
    fleet,
    candidates,
    *,
    reserved:set[int]|None=None,
    pressure:float=1.0,
    max_stops:int=12,
    start_position=None,
    start_profile:dict[str,Any]|None=None,
    sector:tuple[int,int,float]|None=None,
) -> ProbeRoute|None:
    """
    Build a forward-only exploration campaign.

    Primary objective: maximize UNIQUE worlds visited before probe termination.
    Secondary objectives: low travel distance, geographic sector separation,
    and continued outward movement.

    This is deliberately not a round-trip route.
    """
    reserved=set(reserved or ())
    remaining=[
        p for p in candidates
        if int(p.id) not in reserved and not p.observed
    ]
    if not remaining:
        return None

    sector=sector or scout_sector(state,fleet)
    sector_index,sector_count,sector_angle=sector
    center=_empire_center(state)
    pos=start_position or fleet.position
    profile=dict(start_profile or ((fleet.native or {}).get("fuel_profile") or {}))
    flags=(fleet.native or {}).get("race_fuel_flags",{})
    ife=bool(flags.get("ife")); ce=bool(flags.get("ce"))
    free=highest_zero_fuel_warp(profile) if profile else 0

    route=[]
    total_distance=0.0
    est_turns=0.0

    for step in range(max(1,int(max_stops))):
        feasible=[]
        for p in remaining:
            d=distance(pos,p.position)
            if profile:
                w=scout_one_way_warp(
                    profile,d,ife=ife,ce=ce,pressure=pressure
                )
                if w is None:
                    continue
            else:
                w=7 if d<120 else 6

            # Dense chains are valuable because a single probe can cover more
            # worlds before fuel/lifetime is exhausted.
            cluster=_cluster_count(p,remaining)
            outward=distance(center,p.position)
            sector_fit=math.cos(_angle_delta(_angle(center,p.position),sector_angle))

            # Number of future possibilities dominates the score. Distance is
            # still meaningful so we do not jump across empty space needlessly.
            score=(
                cluster*7.5
                + min(outward,300.0)*0.07
                + sector_fit*18.0
                - d*0.22
            )
            # First leg should be practical; later legs can push farther outward.
            if step==0:
                score-=max(0.0,d-120.0)*0.15
            feasible.append((score,-d,p,w))

        if not feasible:
            break

        feasible.sort(key=lambda x:(x[0],x[1]),reverse=True)
        _,_,target,warp=feasible[0]
        leg=distance(pos,target.position)
        total_distance+=leg
        est_turns+=leg/max(1.0,float(warp*warp))
        route.append(int(target.id))

        if profile:
            profile=profile_after_scout_leg(
                profile,leg,warp,ife=ife
            )
        pos=target.position
        remaining=[p for p in remaining if int(p.id)!=int(target.id)]

        if not remaining:
            break

    if not route:
        return None

    return ProbeRoute(
        planet_ids=route,
        expected_discoveries=len(route),
        total_distance=round(total_distance,2),
        estimated_turns=round(est_turns,2),
        sector_index=sector_index,
        sector_count=sector_count,
        terminal=True,
        free_cruise_warp=free,
    )


def evaluate_recon_refuel(
    state,
    fleet,
    candidates,
    *,
    reserved:set[int]|None=None,
    pressure:float=1.0,
    max_refuel_distance:float=150.0,
    minimum_route_gain:int=5,
):
    """
    Return a refuel plan only when the detour has a clear exploration payoff.

    Fuel-Mizer/free-Warp-4 probes NEVER return just for fuel.
    Conventional scouts may refuel only when:
      - they currently cannot build a useful route;
      - a reachable refueling base is nearby enough;
      - a full tank from that base unlocks >= minimum_route_gain unknown worlds.
    """
    fp=(fleet.native or {}).get("fuel_profile")
    if not fp:
        return None
    if highest_zero_fuel_warp(fp)>=4:
        return None

    flags=(fleet.native or {}).get("race_fuel_flags",{})
    ife=bool(flags.get("ife")); ce=bool(flags.get("ce"))

    bases=[
        p for p in state.planets
        if p.owner==state.player_id
        and bool(((p.native or {}).get("starbase_capabilities") or {}).get("can_refuel"))
    ]
    best=None
    sector=scout_sector(state,fleet)

    for base in bases:
        bd=distance(fleet.position,base.position)
        if bd<=.5 or bd>float(max_refuel_distance):
            continue
        bw=fastest_fuel_safe_warp(fp,bd,'refuel',ife=ife,ce=ce)
        if bw is None:
            continue

        full=dict(fp)
        cap=float(fp.get("fuel_capacity",0) or 0)
        full["fuel"]=cap
        full["effective_fuel"]=cap
        full["at_starbase"]=True

        route=build_probe_route(
            state,fleet,candidates,
            reserved=set(reserved or ()),
            pressure=pressure,
            max_stops=12,
            start_position=base.position,
            start_profile=full,
            sector=sector,
        )
        if route is None or route.expected_discoveries<int(minimum_route_gain):
            continue

        detour_turns=bd/max(1.0,float(bw*bw))
        campaign_turns=detour_turns+max(.1,route.estimated_turns)
        value=route.expected_discoveries/campaign_turns
        candidate={
            "base":base,
            "refuel_warp":int(bw),
            "refuel_distance":round(bd,2),
            "detour_turns":round(detour_turns,2),
            "route":route,
            "discoveries_per_turn":round(value,3),
        }
        if best is None or candidate["discoveries_per_turn"]>best["discoveries_per_turn"]:
            best=candidate

    return best
