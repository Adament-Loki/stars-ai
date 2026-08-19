
from __future__ import annotations

import math
from ..models import GameState, OrderSet
from ..persona import StrategicPlan
from ..util import distance
from ..warp_policy import mission_warp
from ..fuel_planner import mission_reachable, highest_zero_fuel_warp
from ..exploration_router import (
    build_probe_route,
    evaluate_recon_refuel,
    scout_sector,
)


def _probe_fleets(state):
    return [
        f for f in state.fleets
        if f.owner==state.player_id
        and (
            f.role=="scout"
            or (f.role=="unknown" and float(getattr(f,"combat_power",0.0) or 0.0)<=0.0)
        )
    ]


def add_exploration_orders(
    state: GameState,
    orders: OrderSet,
    plan: StrategicPlan | None = None,
    memory=None,
) -> None:
    """
    v7.1 exploration is a persistent ROUTE campaign, not a yearly nearest-target
    choice. Each probe receives up to 12 forward unknown worlds and normally
    ends its useful life on the frontier rather than returning home.
    """
    probes=_probe_fleets(state)
    state.native["recon_route_managed_fleets"]=[int(f.id) for f in probes]
    if not probes:
        return

    pressure=float(
        ((state.native or {}).get("strategic_watchdog") or {}).get(
            "exploration_pressure",1.0
        )
    )
    scout_weight=(
        (plan.objective("scout")*plan.mission("scan")) if plan else 1.0
    )*pressure

    recent_targets={
        int(x) for x in (state.native or {}).get("recent_scan_targets",[])
    }

    if memory is not None:
        memory.prune_scout_routes(state)

    # Every genuine current/persistent observation is excluded. Recent unresolved
    # targets are also excluded from NEW routes, though a fleet may continue its
    # own already-persisted route.
    unknown_all=[p for p in state.planets if not p.observed]
    if not unknown_all:
        return

    known_enemy=any(
        p.owner not in (None,state.player_id)
        for p in state.planets
    )
    eligible_aux=[
        f for f in state.fleets
        if state.year<=2410
        and not known_enemy
        and f.owner==state.player_id
        and f.destination_planet_id is None
        and f.role=="combat"
    ]
    exploration_assets=max(1,len(probes)+len(eligible_aux))
    route_limit=min(
        12,
        max(1,math.ceil(len(unknown_all)/exploration_assets)),
    )

    # Reserve one genuinely local leftover for each early combat auxiliary.
    # Dedicated probes then route around those worlds instead of consuming every
    # nearby target and leaving the auxiliary with only a distant impossible leg.
    aux_targets={}
    aux_reserved=set()
    for aux in eligible_aux:
        nearby=[
            p for p in unknown_all
            if p.id not in aux_reserved
            and distance(aux.position,p.position)<=70.0
            and mission_reachable(aux,p.position,"scan")
        ]
        if nearby:
            target=min(nearby,key=lambda p:distance(aux.position,p.position))
            aux_targets[int(aux.id)]=int(target.id)
            aux_reserved.add(int(target.id))

    assigned={
        int(o.payload["destination_planet_id"])
        for o in orders.orders
        if o.payload.get("destination_planet_id") is not None
    }
    assigned.update(aux_reserved)
    for f in probes:
        if f.destination_planet_id is not None:
            assigned.add(int(f.destination_planet_id))

    # Reserve every future stop in other scouts' persistent routes so sectors do
    # not converge on the same chain over multiple years.
    if memory is not None:
        assigned.update(memory.reserved_scout_route_targets())

    already_moving={
        int(o.payload["fleet_id"]) for o in orders.orders
        if o.kind=="move_fleet" and o.payload.get("fleet_id") is not None
    }

    route_diags=[]

    for probe in probes:
        fid=int(probe.id)
        if fid in already_moving or probe.destination_planet_id is not None:
            continue

        # Remove this probe's own route from the global reservation while we
        # inspect/extend it.
        own_reserved=set()
        if memory is not None:
            own=memory.scout_route(fid)
            if own:
                own_reserved={int(x) for x in own.get("planet_ids",[])}
                assigned.difference_update(own_reserved)

        route_info=memory.scout_route(fid) if memory is not None else None
        route_ids=[
            int(x) for x in (route_info or {}).get("planet_ids",[])
            if any(int(p.id)==int(x) and not p.observed for p in state.planets)
        ][:route_limit]
        if memory is not None and route_info is not None:
            route_info["planet_ids"]=list(route_ids)
            memory.scout_routes[str(fid)]=dict(route_info)

        target=None
        if route_ids:
            candidate=next(
                (p for p in state.planets if p.id==route_ids[0] and not p.observed),
                None,
            )
            if (
                candidate is not None
                and candidate.id not in assigned
                and mission_reachable(probe,candidate.position,"scan")
            ):
                target=candidate
            else:
                route_ids=[]

        if not route_ids:
            candidates=[
                p for p in unknown_all
                if p.id not in assigned and p.id not in recent_targets
            ]
            route=build_probe_route(
                state,probe,candidates,
                reserved=assigned,
                pressure=pressure,
                max_stops=route_limit,
                sector=scout_sector(state,probe),
            )
            if route is not None:
                route_ids=list(route.planet_ids)
                target=next(p for p in state.planets if p.id==route_ids[0])
                if memory is not None:
                    memory.set_scout_route(
                        fid,route_ids,state.year,
                        expected_discoveries=route.expected_discoveries,
                        total_distance=route.total_distance,
                        sector_index=route.sector_index,
                        sector_count=route.sector_count,
                        terminal=True,
                    )
                route_info=route.to_dict()

        if target is not None and route_ids:
            fp=(probe.native or {}).get("fuel_profile") or {}
            free_warp=highest_zero_fuel_warp(fp) if fp else 0
            warp=mission_warp(
                probe,target.position,"scan",pressure=pressure
            )
            route_remaining=len(route_ids)
            orders.add(
                "move_fleet",
                {
                    "fleet_id":fid,
                    "destination_planet_id":target.id,
                    "warp":warp,
                    "mission":"scan",
                    "route_managed":True,
                    "route_remaining":route_remaining,
                    "route_planet_ids":list(route_ids),
                    "route_expected_discoveries":int(
                        (route_info or {}).get("expected_discoveries",route_remaining)
                    ),
                    "route_terminal":True,
                    "free_cruise_warp":free_warp,
                    "exploration_pressure":pressure,
                },
                (
                    f"{plan.persona_name + ': ' if plan else ''}"
                    f"probe campaign -> {target.name}; "
                    f"{route_remaining} unknown worlds remain on persistent forward route"
                    +(
                        f"; free cruise Warp {free_warp}"
                        if free_warp>=2 else ""
                    )
                    +". No automatic return-to-base reserve."
                ),
                priority=max(70,int(92*scout_weight)),
            )
            assigned.update(route_ids)
            already_moving.add(fid)
            route_diags.append({
                "fleet_id":fid,
                "next":target.id,
                "remaining":route_remaining,
                "free_cruise_warp":free_warp,
            })
            continue

        # No one-way route from current fuel. Refuel is NOT a safety reflex.
        # It must demonstrably unlock a substantial future exploration campaign.
        candidates=[
            p for p in unknown_all
            if p.id not in assigned and p.id not in recent_targets
        ]
        refuel=evaluate_recon_refuel(
            state,probe,candidates,
            reserved=assigned,
            pressure=pressure,
            max_refuel_distance=150.0,
            minimum_route_gain=5,
        )
        if refuel is not None:
            base=refuel["base"]
            route=refuel["route"]
            if memory is not None:
                memory.set_scout_route(
                    fid,route.planet_ids,state.year,
                    expected_discoveries=route.expected_discoveries,
                    total_distance=route.total_distance,
                    sector_index=route.sector_index,
                    sector_count=route.sector_count,
                    terminal=True,
                    awaiting_refuel=True,
                )
            orders.add(
                "move_fleet",
                {
                    "fleet_id":fid,
                    "destination_planet_id":base.id,
                    "warp":int(refuel["refuel_warp"]),
                    "mission":"refuel_for_scan",
                    "route_managed":True,
                    "planned_route_after_refuel":list(route.planet_ids),
                    "planned_discoveries_after_refuel":route.expected_discoveries,
                    "refuel_distance":refuel["refuel_distance"],
                    "discoveries_per_turn_after_refuel":refuel["discoveries_per_turn"],
                },
                (
                    f"Strategic recon refuel at {base.name}: {refuel['refuel_distance']:.1f} ly "
                    f"detour unlocks a planned {route.expected_discoveries}-world forward route "
                    f"(~{refuel['discoveries_per_turn']:.2f} discoveries/turn)."
                ),
                priority=max(75,int(90*scout_weight)),
            )
            assigned.update(route.planet_ids)
            already_moving.add(fid)
        else:
            if memory is not None:
                memory.clear_scout_route(fid)
            route_diags.append({
                "fleet_id":fid,
                "next":None,
                "remaining":0,
                "reason":"no one-way route and no high-value refuel campaign",
            })

    state.native["probe_route_diagnostics"]=route_diags

    # Early-game auxiliary reconnaissance remains useful, but only with idle
    # COMBAT hulls and only while no enemy is known. Dedicated probes own the
    # persistent sectors; auxiliaries take nearby leftovers.
    if state.year<=2410 and not known_enemy:
        auxiliaries=[
            f for f in state.fleets
            if f.owner==state.player_id
            and f.destination_planet_id is None
            and f.role=="combat"
            and f.id not in already_moving
        ]
        for fleet in auxiliaries:
            reserved_pid=aux_targets.get(int(fleet.id))
            target=next(
                (p for p in unknown_all if p.id==reserved_pid),
                None,
            )
            if target is None:
                candidates=[
                    p for p in unknown_all
                    if p.id not in assigned
                    and p.id not in recent_targets
                    and distance(fleet.position,p.position)<=70.0
                    and mission_reachable(fleet,p.position,"scan")
                ]
                if not candidates:
                    continue
                target=min(candidates,key=lambda p:distance(fleet.position,p.position))
            assigned.add(target.id)
            orders.add(
                "move_fleet",
                {
                    "fleet_id":fleet.id,
                    "destination_planet_id":target.id,
                    "warp":mission_warp(
                        fleet,target.position,"scan",pressure=pressure
                    ),
                    "mission":"scan",
                    "auxiliary_recon":True,
                },
                (
                    f"{plan.persona_name + ': ' if plan else ''}"
                    "short-range auxiliary reconnaissance with idle combat fleet."
                ),
                priority=max(45,int(54*scout_weight)),
            )
            already_moving.add(fleet.id)


def deconflict_recon_orders(state: GameState, orders: OrderSet) -> None:
    """Final safety barrier: at most one recon fleet is assigned per unknown planet.

    If duplicate scan orders somehow enter from different strategy passes, keep the
    higher-priority one and retarget later duplicates to the nearest unreserved,
    fuel-reachable unknown. Existing active destinations are reserved too.
    """
    planet_by_id={p.id:p for p in state.planets}
    fleet_by_id={f.id:f for f in state.fleets if f.owner==state.player_id}
    reserved=set()
    for f in fleet_by_id.values():
        if f.role not in ("scout","unknown"):
            continue
        if f.destination_planet_id is not None:
            reserved.add(int(f.destination_planet_id))
        wps=(f.native or {}).get("waypoints") or []
        if len(wps)>=2 and wps[1].get("position_object") is not None:
            reserved.add(int(wps[1]["position_object"]))

    scan_orders=[o for o in orders.orders if o.kind=="move_fleet" and str(o.payload.get("mission","")) in ("scan","recon")]
    scan_orders.sort(key=lambda o:o.priority,reverse=True)
    used=set(reserved)
    for o in scan_orders:
        fid=int(o.payload.get("fleet_id",-1)); f=fleet_by_id.get(fid)
        target_id=int(o.payload.get("destination_planet_id",-1))
        if target_id not in used:
            used.add(target_id); continue
        if f is None:
            continue
        alternatives=[
            p for p in state.planets
            if not p.observed and p.id not in used and mission_reachable(f,p.position,"scan")
        ]
        if not alternatives:
            o.payload["deconflicted_hold"]=True
            o.payload["warp"]=1
            o.reason += " Recon deconfliction found no unique fuel-safe unknown target; native writer should skip this duplicate move."
            continue
        target=min(alternatives,key=lambda p:distance(f.position,p.position))
        old=planet_by_id.get(target_id)
        o.payload["destination_planet_id"]=target.id
        o.payload["warp"]=mission_warp(f,target.position,"scan",pressure=float(((state.native or {}).get("strategic_watchdog") or {}).get("exploration_pressure",1.0)))
        o.payload["deconflicted_from_planet_id"]=target_id
        o.reason += f" Recon deconfliction retargeted from {old.name if old else target_id} to {target.name}."
        used.add(target.id)

