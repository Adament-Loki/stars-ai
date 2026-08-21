
from __future__ import annotations

from dataclasses import dataclass, asdict
import math
from typing import Any

from .util import distance
from .empire_geometry import distance_from_homeworld, homeworld_center
from .expansion_network import evaluate_expansion_network
from .fuel_planner import (
    reconnaissance_warp,
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


# A probe should finish surveying the neighborhood it is already in before it
# leaps to a distant, globally attractive cluster.  The route builder applies
# this per leg, so every later waypoint is chosen near the previous waypoint
# as well as the first waypoint being chosen near the scout's present position.
SCOUT_LOCAL_HOP_MIN_LY=60.0
SCOUT_LOCAL_HOP_NEAREST_MULTIPLIER=1.6


def _local_hop_candidates(feasible:list[tuple]) -> list[tuple]:
    """Keep a probe in its nearest unexplored neighborhood for this leg.

    Sector allocation and cluster value still rank worlds *inside* this pool.
    A distant world remains eligible only after no nearby unexplored world is
    available, preventing long cross-map jumps that skip a local survey chain.
    """
    if not feasible:
        return []
    nearest=min(-float(entry[1]) for entry in feasible)
    maximum=max(
        SCOUT_LOCAL_HOP_MIN_LY,
        nearest*SCOUT_LOCAL_HOP_NEAREST_MULTIPLIER,
    )
    return [entry for entry in feasible if -float(entry[1])<=maximum]


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


def exploration_promotion_target(state, planet, *, network=None) -> dict[str, Any]:
    """Classify an unknown world as the next P1/P2/P3/P4 relay opportunity.

    The score is deliberately a bonus to the local fuel-safe route heuristic,
    not permission to jump across nearby unexplored systems. This keeps scouts
    clustered while making their *direction* serve the promotion pipeline.
    """
    network=network or evaluate_expansion_network(state)
    if network.homeworld_id is None:
        return {"tier":1,"label":"P1","score":0.0,"parent_id":None,"parent_rank":None}
    planets={int(p.id):p for p in state.planets}
    home=planets.get(int(network.homeworld_id))
    if home is None:
        return {"tier":1,"label":"P1","score":0.0,"parent_id":None,"parent_rank":None}

    home_distance=float(distance(home.position,planet.position))
    ring=max(1,int(math.ceil(home_distance/max(1.0,float(network.ring_hop_ly)))))
    p1s=[h for h in network.hubs if int(getattr(h,"promotion_tier",3) or 3)==1]
    p2s=[h for h in network.hubs if int(getattr(h,"promotion_tier",3) or 3)==2]

    # Finish the compact P1 constellation first. The score is highest for a
    # standard relay hop rather than for a far edge of the homeworld's range.
    if len(p1s)<int(network.layer1_target_count) and ring==1:
        radial=max(0.0,1.0-abs(home_distance-130.0)/100.0)
        return {
            "tier":1,"label":"P1","score":18.0+8.0*radial,
            "parent_id":int(home.id),"parent_rank":1,
        }

    def child_count(parent_id:int, tier:int) -> int:
        return sum(
            1 for hub in network.hubs
            if int(getattr(hub,"promotion_tier",99) or 99)==tier
            and int(getattr(hub,"promotion_parent_id",-1) or -1)==int(parent_id)
        )

    parent_pool = p1s if ring >= 2 else []
    desired_tier=2
    if ring >= 3 and p2s:
        parent_pool=p2s
        desired_tier=3
    if ring >= 4:
        p3s=[h for h in network.hubs if int(getattr(h,"promotion_tier",99) or 99)==3]
        if p3s:
            parent_pool=p3s
            desired_tier=4

    choices=[]
    for parent_hub in parent_pool:
        parent=planets.get(int(parent_hub.planet_id))
        if parent is None:
            continue
        parent_distance=float(distance(parent.position,planet.position))
        if not 60.0<=parent_distance<=200.0:
            continue
        # P1s have an explicit 2-3 P2 program. Deeper rings use the same
        # compact relay shape but no artificial global quota.
        room=(
            max(0,int(network.layer2_target_children_per_parent)-child_count(int(parent.id),2))
            if desired_tier==2 else 1
        )
        if room<=0:
            continue
        radial=max(0.0,1.0-abs(parent_distance-130.0)/100.0)
        choices.append((float(parent_hub.overall_value)+0.25*radial,parent_hub,parent_distance,room))
    if choices:
        _, parent_hub, parent_distance, room=max(choices,key=lambda row:row[0])
        tier_score={2:15.0,3:10.0,4:7.0}.get(desired_tier,5.0)
        return {
            "tier":desired_tier,
            "label":f"P{desired_tier}",
            "score":tier_score+5.0*max(0.0,1.0-abs(parent_distance-130.0)/100.0)+min(3,room),
            "parent_id":int(parent_hub.planet_id),
            "parent_rank":getattr(parent_hub,"promotion_rank",None),
        }

    fallback=min(4,ring)
    return {"tier":fallback,"label":f"P{fallback}","score":max(0.0,5.0-fallback),"parent_id":None,"parent_rank":None}


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
            warp=reconnaissance_warp(
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
    max_support_distance:float|None=None,
) -> ProbeRoute|None:
    """
    Build a fuel-safe exploration campaign.

    Primary objective: maximize UNIQUE worlds visited before probe termination.
    Secondary objectives: low travel distance, geographic sector separation,
    and proximity to the expanding owned-planet network.  ``max_support_distance``
    is an optional caller-specific policy cap; the general explorer deliberately
    has no hard territorial radius.  A scout that can safely make the next leg
    is allowed to extend the frontier.
    """
    reserved=set(reserved or ())
    remaining=[
        p for p in candidates
        if int(p.id) not in reserved
        and _needs_recon(p)
        and (
            max_support_distance is None
            or _support_distance(state,p.position)<=float(max_support_distance)
        )
    ]
    if not remaining:
        return None

    sector=sector or scout_sector(state,fleet)
    sector_index,sector_count,sector_angle=sector
    center=homeworld_center(state)
    turn=max(0,int(state.year)-2400)
    promotion_network=evaluate_expansion_network(state)
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
                w=reconnaissance_warp(
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
            promotion=exploration_promotion_target(
                state,p,network=promotion_network
            )

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
                + float(promotion["score"])
            )
            # First leg should be practical; later legs can push farther outward.
            if step==0:
                score-=max(0.0,d-120.0)*0.15
            feasible.append((score,-d,p,w,promotion))

        if not feasible:
            break

        # Do not let aggregate cluster/sector score make this leg jump over
        # closer unexplored worlds.  ``pos`` advances after every selection,
        # giving a compact, contiguous survey chain.
        feasible=_local_hop_candidates(feasible)
        feasible.sort(key=lambda x:(x[0],x[1]),reverse=True)
        _,_,target,warp,promotion=feasible[0]
        leg=distance(pos,target.position)
        total_distance+=leg
        est_turns+=leg/max(1.0,float(warp*warp))
        route.append(int(target.id))
        waypoint_specs.append({
            "planet_id":int(target.id),
            "warp":int(warp),
            "task":0,
            "promotion_tier":int(promotion["tier"]),
            "promotion_parent_id":promotion["parent_id"],
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
