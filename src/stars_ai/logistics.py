
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any
from .util import distance

class RouteMode(str, Enum):
    DIRECT="direct"
    PLANET_HOP="planet_hop"
    REFUEL="refuel"
    GATE="gate"
    TANKER="tanker"
    REDUCE_WARP="reduce_warp"
    DELAY="delay"

@dataclass
class RoutePlan:
    fleet_id: int
    destination_planet_id: int
    mode: RouteMode
    warp: int
    path_planet_ids: list[int]
    risk: float
    turns: float
    refuel_stops: list[int]
    concealment_stops: list[int]
    detection_risk: float
    reason: str

def _has_starbase(p: Any) -> bool:
    n = getattr(p, "native", {}) or {}
    return bool(n.get("starbase", n.get("has_starbase", False)))

def _has_gate(p: Any) -> bool:
    n = getattr(p, "native", {}) or {}
    return bool(n.get("gate"))

def _is_owned(state: Any, p: Any) -> bool:
    return getattr(p, "owner", None) == state.player_id

def planet_concealment_value(
    planet: Any,
    *,
    enemy_penetrating_scanner_probability: float = 0.0,
) -> float:
    """
    A planet can mask fleets in orbit from observers that lack penetrating
    scanners. The value falls as confidence rises that the enemy can penetrate
    planetary cover.
    """
    p = max(0.0, min(1.0, enemy_penetrating_scanner_probability))
    strategic = float((getattr(planet, "native", {}) or {}).get("strategic_value", 0.5))
    return max(0.0, min(1.0, (1.0-p) * (0.75 + 0.25*strategic)))

def route_detection_risk(
    *,
    deep_space_legs: int,
    planet_cover_legs: int,
    enemy_penetrating_scanner_probability: float,
) -> float:
    p = max(0.0, min(1.0, enemy_penetrating_scanner_probability))
    deep = max(0, deep_space_legs)
    covered = max(0, planet_cover_legs)
    if deep + covered == 0:
        return 0.0
    weighted = deep * 1.0 + covered * (0.25 + 0.75*p)
    return max(0.0, min(1.0, weighted / max(1, deep+covered)))

def _candidate_planet_hops(
    state: Any,
    fleet: Any,
    destination: Any,
    fuel_range_ly: float,
    *,
    enemy_penetrating_scanner_probability: float,
) -> list[Any]:
    """
    Greedy forward-progress candidates that are reachable and move the fleet
    closer to its destination. Starbase worlds get a large bonus because a
    fleet arriving there is immediately refueled.
    """
    owned = [p for p in state.planets if _is_owned(state,p)]
    current = fleet.position
    d0 = distance(current, destination.position)
    scored = []
    for p in owned:
        leg = distance(current, p.position)
        remaining = distance(p.position, destination.position)
        if leg <= 0 or leg > fuel_range_ly or remaining >= d0:
            continue
        conceal = planet_concealment_value(
            p,
            enemy_penetrating_scanner_probability=enemy_penetrating_scanner_probability,
        )
        starbase = 1.0 if _has_starbase(p) else 0.0
        progress = max(0.0, (d0-remaining)/max(1.0,d0))
        # Refuel is intentionally very valuable: arriving at a starbase resets
        # the fleet's fuel constraint for the following leg.
        score = 1.25*starbase + 0.75*conceal + 0.8*progress - leg/max(1.0,fuel_range_ly)*0.15
        scored.append((score,p))
    return [p for _,p in sorted(scored,key=lambda x:x[0],reverse=True)]

def plan_route(
    state: Any,
    fleet: Any,
    destination: Any,
    *,
    fuel_range_ly: float | None=None,
    enemy_penetrating_scanner_probability: float=0.0,
    stealth_priority: float=0.5,
) -> RoutePlan:
    """
    Route planning is not shortest-path only.

    It weighs:
    - fuel range,
    - instant refuel at owned starbases,
    - concealment from non-penetrating scanners while orbiting planets,
    - gate access,
    - travel time and operational risk.
    """
    stealth_priority=max(0.0,min(1.0,stealth_priority))
    d=distance(fleet.position,destination.position)
    rng=fuel_range_ly or float((fleet.native or {}).get("fuel_range_ly",99999))
    owned=[p for p in state.planets if _is_owned(state,p)]
    gate_worlds=[p for p in owned if _has_gate(p)]

    direct_detection = route_detection_risk(
        deep_space_legs=1, planet_cover_legs=0,
        enemy_penetrating_scanner_probability=enemy_penetrating_scanner_probability
    )

    # Even when direct travel is feasible, a planet/starbase hop can be superior
    # if it adds little distance while providing concealment/refuel.
    hops=_candidate_planet_hops(
        state,fleet,destination,rng,
        enemy_penetrating_scanner_probability=enemy_penetrating_scanner_probability
    )
    if hops and stealth_priority >= 0.45:
        p=hops[0]
        via=distance(fleet.position,p.position)+distance(p.position,destination.position)
        det=route_detection_risk(
            deep_space_legs=1,
            planet_cover_legs=1,
            enemy_penetrating_scanner_probability=enemy_penetrating_scanner_probability
        )
        if via <= d*1.35 or _has_starbase(p):
            refuels=[p.id] if _has_starbase(p) else []
            return RoutePlan(
                fleet.id,destination.id,RouteMode.PLANET_HOP,min(fleet.speed,9),
                [p.id],0.08 if _has_starbase(p) else 0.12,
                max(1.0,via/max(1,fleet.speed**2)),refuels,[p.id],det,
                f"Route through {p.name} for planetary concealment"
                + (" and instant starbase refueling." if _has_starbase(p) else ".")
            )

    if d<=rng:
        return RoutePlan(
            fleet.id,destination.id,RouteMode.DIRECT,min(fleet.speed,9),[],
            0.1,max(1,d/max(1,fleet.speed**2)),[],[],direct_detection,
            "Destination is within direct fuel range; no waypoint gives enough concealment/refuel value to justify the detour."
        )

    if gate_worlds:
        origin=min(gate_worlds,key=lambda p:distance(fleet.position,p.position))
        if distance(fleet.position,origin.position)<=rng:
            return RoutePlan(
                fleet.id,destination.id,RouteMode.GATE,min(fleet.speed,9),
                [origin.id],0.12,1.5,
                [origin.id] if _has_starbase(origin) else [],
                [origin.id],
                route_detection_risk(deep_space_legs=0,planet_cover_legs=1,
                    enemy_penetrating_scanner_probability=enemy_penetrating_scanner_probability),
                "Stage through a gate world; planetary orbit provides concealment and a starbase refuels instantly when present."
            )

    starbase_refuel=[
        p for p in owned
        if _has_starbase(p)
        and distance(fleet.position,p.position)<=rng
        and distance(p.position,destination.position)<d
    ]
    if starbase_refuel:
        stop=min(
            starbase_refuel,
            key=lambda p: (
                distance(fleet.position,p.position)+distance(p.position,destination.position)
                - 80*planet_concealment_value(
                    p,
                    enemy_penetrating_scanner_probability=enemy_penetrating_scanner_probability
                )
            )
        )
        return RoutePlan(
            fleet.id,destination.id,RouteMode.REFUEL,min(fleet.speed,9),
            [stop.id],0.1,2.0,[stop.id],[stop.id],
            route_detection_risk(deep_space_legs=1,planet_cover_legs=1,
                enemy_penetrating_scanner_probability=enemy_penetrating_scanner_probability),
            f"Stage through {stop.name}; its starbase instantly refuels the fleet and the planet can conceal the fleet from non-penetrating scanners."
        )

    if d<=rng*1.35:
        return RoutePlan(
            fleet.id,destination.id,RouteMode.REDUCE_WARP,max(4,min(fleet.speed,7)),
            [],0.25,2.0,[],[],direct_detection,
            "Reduce warp to extend practical range; no reachable starbase/gate waypoint is available."
        )

    return RoutePlan(
        fleet.id,destination.id,RouteMode.DELAY,min(fleet.speed,9),
        [],0.5,999,[],[],direct_detection,
        "No safe logistics path exists; delay until tanker, gate, or starbase staging support becomes available."
    )
