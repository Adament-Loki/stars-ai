
from __future__ import annotations

import math
from ..models import GameState, OrderSet
from ..persona import StrategicPlan
from ..util import distance
from ..empire_geometry import homeworld_center
from ..warp_policy import mission_warp
from ..fuel_planner import mission_reachable, highest_zero_fuel_warp
from ..exploration_router import (
    build_probe_route,
    exploration_promotion_target,
    evaluate_recon_refuel,
    route_waypoint_specs,
    scout_sector,
)
from ..scout_policy import enemy_contact_summary, custom_scout_missions
from ..planetary_scanners import deployed_planetary_sensor_network


MAX_QUEUED_SCOUT_WAYPOINTS=7
# A persistent scout route is deliberately long, so its first choice matters
# disproportionately.  Clear the useful home neighborhood before reserving a
# route that can pull a probe hundreds of light-years away for several years.
# This is a reconnaissance doctrine boundary, not an empire expansion limit:
# once this local backlog is gone, the ordinary fuel-safe frontier router owns
# the rest of the map.
HOME_NEIGHBORHOOD_RECON_RADIUS_LY=100.0
# While this ring contains unobserved worlds, do not queue a chain of outer
# destinations.  Keep two local stops instead: it preserves home-first
# scouting while ensuring a probe which completes a short first leg does not
# sit idle for the host phase before the next AI decision pass.
HOME_CLEARANCE_MAX_ROUTE_STOPS=2


def _needs_recon(planet) -> bool:
    return not planet.observed


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
    Exploration is a native queued-route campaign, not a yearly nearest-target
    choice. Each probe receives up to seven fuel-safe waypoints.  Local worlds
    are strongly preferred, but there is no arbitrary empire-support radius:
    a viable one-way scout route may extend the frontier.
    """
    probes=_probe_fleets(state)
    state.native["recon_route_managed_fleets"]=[int(f.id) for f in probes]
    contact=enemy_contact_summary(state)
    custom_missions=custom_scout_missions(state)
    sensor_network=deployed_planetary_sensor_network(state)
    state.native["scout_exploration_policy"]={
        **contact,
        "classic_exploration_enabled":True,
        "contact_limited_scout_building":bool(contact["enemy_contact"]),
        "custom_missions":custom_missions,
        "planetary_sensor_network":sensor_network,
    }
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

    # A world leaves the reconnaissance pool permanently after its first valid
    # observation. The route scorer prefers nearby supported clusters, but does
    # not strand unexplored outer systems behind an arbitrary distance wall.
    unknown_all=[
        p for p in state.planets
        if _needs_recon(p)
    ]
    if not unknown_all:
        return
    home_center=homeworld_center(state)
    home_unknown_ids={
        int(p.id) for p in unknown_all
        if distance(home_center,p.position)<=HOME_NEIGHBORHOOD_RECON_RADIUS_LY
    }
    home_clearance_active=bool(home_unknown_ids)

    # A pre-existing outer waypoint used to bypass the local-backlog policy:
    # the route was preserved before a newly idle scout could be assigned one
    # of the nearby unknown worlds.  Do not continue spending years on that
    # outer route.  The writer emits the captured Type-3 cancellation only;
    # next turn, after the host has removed the route, the scout receives its
    # ordinary one-stop local survey order.
    cancelled_outer_routes=set()
    if home_clearance_active:
        planets_by_id={int(planet.id):planet for planet in state.planets}
        for probe in probes:
            destination=probe.destination_planet_id
            target=planets_by_id.get(int(destination)) if destination is not None else None
            if target is None or distance(home_center,target.position)<=HOME_NEIGHBORHOOD_RECON_RADIUS_LY:
                continue
            waypoint_count=max(1, int(((probe.native or {}).get("waypoint_count", 1) or 1)))
            orders.add(
                "cancel_scout_route",
                {
                    "fleet_id":int(probe.id),
                    "cancelled_destination_planet_id":int(target.id),
                    "waypoint_count":waypoint_count,
                    "reason":"home_neighborhood_clearance",
                },
                (
                    f"Cancel scout route to {target.name} ({distance(home_center,target.position):.1f} ly): "
                    f"unexplored worlds remain within {HOME_NEIGHBORHOOD_RECON_RADIUS_LY:.0f} ly of home. "
                    "The scout will be reissued a local survey route after the host confirms cancellation."
                ),
                priority=max(94,int(108*scout_weight)),
            )
            cancelled_outer_routes.add(int(probe.id))
            if memory is not None:
                memory.clear_scout_route(int(probe.id))

    border_missions={
        int(mission["target_planet_id"]):mission
        for mission in custom_missions
        if mission.get("target_planet_id") is not None
        and str(mission.get("kind", "")) == "border_recon"
    }

    known_enemy=bool(contact["enemy_contact"])
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
        MAX_QUEUED_SCOUT_WAYPOINTS,
        max(1,math.ceil(len(unknown_all)/exploration_assets)),
    )
    active_route_limit=(
        HOME_CLEARANCE_MAX_ROUTE_STOPS
        if home_clearance_active else route_limit
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
        if f.destination_planet_id is not None and int(f.id) not in cancelled_outer_routes:
            assigned.add(int(f.destination_planet_id))

    # Reserve every future stop in other scouts' persistent routes so sectors do
    # not converge on the same chain over multiple years.  An old outer chain
    # is *not* allowed to reserve the map while an unobserved home-neighborhood
    # world remains: that was the failure mode that left Hell unexplored.
    deferred_outer_route_ids=set()
    if memory is not None:
        persistent_targets=memory.reserved_scout_route_targets()
        if home_clearance_active:
            deferred_outer_route_ids={
                int(pid) for pid in persistent_targets
                if int(pid) not in home_unknown_ids
            }
            persistent_targets={
                int(pid) for pid in persistent_targets
                if int(pid) in home_unknown_ids
            }
        assigned.update(persistent_targets)

    state.native["scout_exploration_policy"].update({
        "home_neighborhood_radius_ly": HOME_NEIGHBORHOOD_RECON_RADIUS_LY,
        "home_neighborhood_unknown_ids": sorted(home_unknown_ids),
        "home_neighborhood_unknown_count": len(home_unknown_ids),
        "home_neighborhood_clearance_active": home_clearance_active,
        "home_clearance_route_stop_cap": (
            HOME_CLEARANCE_MAX_ROUTE_STOPS if home_clearance_active else None
        ),
        "cancelled_outer_route_fleet_ids": sorted(cancelled_outer_routes),
        "deferred_outer_persistent_route_ids": sorted(deferred_outer_route_ids),
    })

    already_moving={
        int(o.payload["fleet_id"]) for o in orders.orders
        if o.kind=="move_fleet" and o.payload.get("fleet_id") is not None
    }

    route_diags=[]

    for probe in probes:
        fid=int(probe.id)
        if fid in cancelled_outer_routes:
            route_diags.append({
                "fleet_id":fid,
                "next":None,
                "remaining":0,
                "reason":"cancelled outer route; local survey will be assigned after host read-back",
            })
            continue
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
            if any(
                int(p.id)==int(x)
                and _needs_recon(p)
                for p in state.planets
            )
        ]
        # Memory is advisory for an idle scout.  Preempt a route that escapes
        # the local clearance ring.  A route made entirely of local worlds is
        # already doing the right work, so retain it intact rather than
        # needlessly throwing away its verified waypoint chain.
        if home_clearance_active:
            has_outer_stop=any(pid not in home_unknown_ids for pid in route_ids)
            if has_outer_stop:
                route_ids=[pid for pid in route_ids if pid in home_unknown_ids]
                route_ids=route_ids[:HOME_CLEARANCE_MAX_ROUTE_STOPS]
        route_ids=route_ids[:route_limit]
        route_specs=route_waypoint_specs(
            state,probe,route_ids,pressure=pressure
        )
        route_ids=[int(x["planet_id"]) for x in route_specs]
        if memory is not None and route_info is not None:
            route_info["planet_ids"]=list(route_ids)
            route_info["waypoints"]=list(route_specs)
            memory.scout_routes[str(fid)]=dict(route_info)

        target=None
        if route_ids:
            candidate=next(
                (p for p in state.planets if p.id==route_ids[0] and _needs_recon(p)),
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
            unassigned_unknown=[
                p for p in unknown_all
                if p.id not in assigned
            ]
            nearby_backlog=[
                p for p in unassigned_unknown
                if distance(home_center,p.position)<=HOME_NEIGHBORHOOD_RECON_RADIUS_LY
            ]
            # Route construction reserves every future stop.  Restrict the
            # candidate pool only while there are unclaimed home-neighborhood
            # worlds, which lets successive probes split the local ring rather
            # than allowing the first sector's far route to monopolize years
            # of scout time.
            home_neighborhood_clearance=bool(nearby_backlog)
            if home_neighborhood_clearance:
                # An unknown world remains unknown even if an earlier route
                # was intended to scan it. Retrying it is strictly better than
                # idling a scout or sending it beyond the home ring.
                candidates=nearby_backlog
            else:
                candidates=[
                    p for p in unassigned_unknown
                    if p.id not in recent_targets
                ]
            # Reserve a scout for a concrete contested-border gap before the
            # general sector route is built.  This is the mobile complement to
            # stationary planetary sensors: normal exploration continues, but
            # we actively obtain intelligence where another empire is present.
            border_candidates=[
                planet for planet in candidates
                if int(planet.id) in border_missions
                and mission_reachable(probe,planet.position,"scan")
            ]
            if home_neighborhood_clearance:
                reachable_local=[
                    planet for planet in candidates
                    if mission_reachable(probe,planet.position,"scan")
                ]
                if reachable_local:
                    # The home ring is a coverage problem, not a sector-score
                    # problem.  Start with the closest unobserved world to the
                    # actual homeworld, then use fleet distance as a tie-break.
                    target=min(
                        reachable_local,
                        key=lambda planet:(
                            distance(home_center,planet.position),
                            distance(probe.position,planet.position),
                            int(planet.id),
                        ),
                    )
                    route=build_probe_route(
                        state,probe,candidates,reserved=assigned,
                        pressure=pressure,
                        max_stops=HOME_CLEARANCE_MAX_ROUTE_STOPS,
                        sector=scout_sector(state,probe),
                        # Preserve the home-first choice above, then add one
                        # fuel-safe local continuation so arrival does not
                        # leave the scout idle until next turn's decision.
                        first_planet_id=int(target.id),
                    )
                else:
                    route=None
            elif border_candidates:
                target=min(
                    border_candidates,
                    key=lambda planet:distance(probe.position,planet.position),
                )
                route=build_probe_route(
                    state,probe,[target],reserved=assigned,
                    pressure=pressure,max_stops=1,
                    sector=scout_sector(state,probe),
                )
            else:
                route=build_probe_route(
                    state,probe,candidates,
                    reserved=assigned,
                    pressure=pressure,
                    max_stops=active_route_limit,
                    sector=scout_sector(state,probe),
                )
            if route is not None:
                route_ids=list(route.planet_ids)
                route_specs=list(route.waypoints or [])
                target=next(p for p in state.planets if p.id==route_ids[0])
                if memory is not None:
                    memory.set_scout_route(
                        fid,route_ids,state.year,
                        expected_discoveries=route.expected_discoveries,
                        total_distance=route.total_distance,
                        sector_index=route.sector_index,
                        sector_count=route.sector_count,
                        terminal=True,
                        waypoints=route_specs,
                    )
                route_info=route.to_dict()

        if target is not None and route_ids:
            target_promotion=exploration_promotion_target(state,target)
            fp=(probe.native or {}).get("fuel_profile") or {}
            free_warp=highest_zero_fuel_warp(fp) if fp else 0
            warp=int(route_specs[0]["warp"])
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
                    "route_waypoints":list(route_specs),
                    "route_expected_discoveries":int(
                        (route_info or {}).get("expected_discoveries",route_remaining)
                    ),
                    "route_terminal":True,
                    "free_cruise_warp":free_warp,
                    "exploration_pressure":pressure,
                    "promotion_target":target_promotion,
                    "recon_focus":(
                        "contested_border_intel"
                        if int(target.id) in border_missions else "frontier_exploration"
                    ),
                    "home_neighborhood_clearance": bool(
                        distance(home_center,target.position)<=HOME_NEIGHBORHOOD_RECON_RADIUS_LY
                    ),
                    "border_mission":border_missions.get(int(target.id)),
                },
                (
                    f"{plan.persona_name + ': ' if plan else ''}"
                    f"probe campaign -> {target.name}; "
                    f"{target_promotion['label']} promotion target; "
                    f"{route_remaining} unknown worlds remain on persistent forward route"
                    +(
                        f"; free cruise Warp {free_warp}"
                        if free_warp>=2 else ""
                    )
                    +". Route prefers nearby supported worlds but may extend the frontier when fuel-safe."
                    +(
                        f" Home-neighborhood clearance is active inside {HOME_NEIGHBORHOOD_RECON_RADIUS_LY:.0f} ly."
                        if distance(home_center,target.position)<=HOME_NEIGHBORHOOD_RECON_RADIUS_LY else ""
                    )
                    +(
                        " Contested-border reconnaissance is prioritized because no live penetrating planetary sensor covers this target."
                        if int(target.id) in border_missions else ""
                    )
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
                "promotion_target":target_promotion,
            })
            continue

        # No one-way route from current fuel. Refuel is NOT a safety reflex.
        # It must demonstrably unlock a substantial future exploration campaign.
        candidates=[
            p for p in unknown_all
            if p.id not in assigned
        ]
        if home_clearance_active:
            candidates=[p for p in candidates if int(p.id) in home_unknown_ids]
        else:
            candidates=[p for p in candidates if p.id not in recent_targets]
        refuel=evaluate_recon_refuel(
            state,probe,candidates,
            reserved=assigned,
            pressure=pressure,
            max_refuel_distance=150.0,
            # When the home ring still contains an unknown world, bringing a
            # low-fuel scout back to a base only needs to unlock one local
            # observation.  The old five-world threshold left P2's scout idle
            # even though refueling was its only productive recovery action.
            minimum_route_gain=1 if home_clearance_active else 5,
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
                    waypoints=list(route.waypoints or []),
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
                    "planned_route_waypoints":list(route.waypoints or []),
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
        for wp in wps[1:]:
            if wp.get("position_object") is not None:
                reserved.add(int(wp["position_object"]))

    scan_orders=[o for o in orders.orders if o.kind=="move_fleet" and str(o.payload.get("mission","")) in ("scan","recon")]
    scan_orders.sort(key=lambda o:o.priority,reverse=True)
    used=set(reserved)
    for o in scan_orders:
        fid=int(o.payload.get("fleet_id",-1)); f=fleet_by_id.get(fid)
        target_id=int(o.payload.get("destination_planet_id",-1))
        if target_id not in used:
            used.update(
                int(x) for x in o.payload.get("route_planet_ids",[target_id])
            )
            continue
        if f is None:
            continue
        alternatives=[
            p for p in state.planets
            if _needs_recon(p)
            and p.id not in used
            and mission_reachable(f,p.position,"scan")
        ]
        if not alternatives:
            o.payload["deconflicted_hold"]=True
            o.payload["warp"]=1
            o.reason += " Recon deconfliction found no unique fuel-safe unknown target; native writer should skip this duplicate move."
            continue
        pressure=float(
            ((state.native or {}).get("strategic_watchdog") or {}).get(
                "exploration_pressure",1.0
            )
        )
        route=build_probe_route(
            state,f,alternatives,
            reserved=used,
            pressure=pressure,
            max_stops=MAX_QUEUED_SCOUT_WAYPOINTS,
            sector=scout_sector(state,f),
        )
        if route is None:
            o.payload["deconflicted_hold"]=True
            o.payload["warp"]=1
            o.reason += " Recon deconfliction could not build a unique supported route."
            continue
        target=planet_by_id[int(route.planet_ids[0])]
        old=planet_by_id.get(target_id)
        o.payload["destination_planet_id"]=target.id
        o.payload["warp"]=int(route.waypoints[0]["warp"])
        o.payload["route_planet_ids"]=list(route.planet_ids)
        o.payload["route_waypoints"]=list(route.waypoints or [])
        o.payload["route_remaining"]=len(route.planet_ids)
        o.payload["deconflicted_from_planet_id"]=target_id
        o.reason += f" Recon deconfliction retargeted from {old.name if old else target_id} to {target.name}."
        used.update(int(x) for x in route.planet_ids)
