
from __future__ import annotations

from dataclasses import dataclass, asdict
import math
from typing import Any

from .util import distance
from .empire_geometry import distance_from_homeworld, homeworld_center
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
    waypoints:list[dict[str,int]]|None=None

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


def _needs_recon(planet) -> bool:
    # Exploration is discovery, not periodic re-survey. Persistent memory makes
    # a planet observed forever once a scout has supplied environmental data.
    return not planet.observed


def _support_distance(state, position) -> float:
    owned=[p for p in state.planets if p.owner==state.player_id]
    return min(
        (distance(position,p.position) for p in owned),
        default=0.0,
    )


def route_waypoint_specs(
    state,
    fleet,
    planet_ids,
    *,
    pressure:float=1.0,
    start_position=None,
    start_profile:dict[str,Any]|None=None,
) -> list[dict[str,int]]:
    """Rebuild a persisted planet chain into cumulative fuel-safe native legs."""
    planets={int(p.id):p for p in state.planets}
    pos=start_position or fleet.position
    profile=dict(start_profile or ((fleet.native or {}).get("fuel_profile") or {}))
    flags=(fleet.native or {}).get("race_fuel_flags",{})
    ife=bool(flags.get("ife")); ce=bool(flags.get("ce"))
    out=[]
    for raw_pid in planet_ids:
        pid=int(raw_pid)
        target=planets.get(pid)
        if target is None:
            break
        leg=distance(pos,target.position)
        if profile:
            warp=scout_one_way_warp(
                profile,leg,ife=ife,ce=ce,pressure=pressure
            )
            if warp is None:
                break
            profile=profile_after_scout_leg(
                profile,leg,warp,ife=ife
            )
        else:
            warp=7 if leg<120 else 6
        out.append({"planet_id":pid,"warp":int(warp),"task":0})
        pos=target.position
    return out


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
    max_support_distance:float=300.0,
) -> ProbeRoute|None:
    """
    Build a fuel-safe exploration campaign inside the supported frontier.

    Primary objective: maximize UNIQUE worlds visited before probe termination.
    Secondary objectives: low travel distance, geographic sector separation,
    and proximity to the expanding owned-planet network.
    """
    reserved=set(reserved or ())
    remaining=[
        p for p in candidates
        if int(p.id) not in reserved
        and _needs_recon(p)
        and _support_distance(state,p.position)<=float(max_support_distance)
    ]
    if not remaining:
        return None

    sector=sector or scout_sector(state,fleet)
    sector_index,sector_count,sector_angle=sector
    center=homeworld_center(state)
    turn=max(0,int(state.year)-2400)
    pos=start_position or fleet.position
    profile=dict(start_profile or ((fleet.native or {}).get("fuel_profile") or {}))
    flags=(fleet.native or {}).get("race_fuel_flags",{})
    ife=bool(flags.get("ife")); ce=bool(flags.get("ce"))
    free=highest_zero_fuel_warp(profile) if profile else 0

    route=[]
    waypoint_specs=[]
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
            support_distance=_support_distance(state,p.position)
            home_distance=distance_from_homeworld(state,p.position)
            sector_fit=math.cos(_angle_delta(_angle(center,p.position),sector_angle))

            # Number of future possibilities dominates the score. Distance is
            # still meaningful so we do not jump across empty space needlessly.
            if turn<=25:
                home_penalty=home_distance*.16
                local_bonus=max(0.0,180.0-home_distance)*.25
            else:
                home_penalty=home_distance*.05
                local_bonus=max(0.0,120.0-home_distance)*.08
            score=(
                cluster*7.5
                + sector_fit*18.0
                + local_bonus
                - d*0.25
                - support_distance*0.05
                - home_penalty
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
        waypoint_specs.append({
            "planet_id":int(target.id),
            "warp":int(warp),
            "task":0,
        })

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
        waypoints=waypoint_specs,
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
