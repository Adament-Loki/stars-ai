
from __future__ import annotations

import math
from ..diplomacy import allied_player_ids
from ..models import GameState, OrderSet
from ..persona import StrategicPlan
from ..util import distance
from ..empire_geometry import homeworld_center
from ..expansion_network import evaluate_expansion_network
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
from ..turn_state import mapping as turn_mapping, sequence as turn_sequence


MAX_QUEUED_SCOUT_WAYPOINTS=7
# A persistent scout route is deliberately long, so its first choice matters
# disproportionately.  Survey outward from the homeworld, and do not reserve a
# route beyond this radius while any world inside it is still unexplored.
# A human opening stays inside about 400 ly through turn 29; matching that
# keeps the early scout fleet on the neighborhood instead of a cross-map
# frontier chain.  Once this backlog is gone, the fuel-safe frontier router
# owns the rest of the map.
HOME_NEIGHBORHOOD_RECON_RADIUS_LY=400.0
# Idle armed ships may fill a short local gap during the opening, but they
# are not scouts.  Two of them, and only through turn 25, is the limit.
EARLY_FIGHTER_SCOUT_TURN=25
EARLY_FIGHTER_SCOUT_CAP=2
EARLY_FIGHTER_SCOUT_RANGE_LY=70.0
# While this ring contains unobserved worlds, the route stays inside it and
# walks outward from the homeworld. The chain is long enough that one scout
# keeps discovering worlds through the turn-40 exploration window instead of
# stopping after a single hop and waiting to be retargeted.
HOME_CLEARANCE_MAX_ROUTE_STOPS=MAX_QUEUED_SCOUT_WAYPOINTS
PRIORITY_HUB_RECON_RADIUS_LY=85.0


def _priority_survey_hubs(state):
    """Return mature P1/P2 relay centers that must clear local unknowns first."""
    by_id={int(planet.id):planet for planet in state.planets}
    hubs=[]
    try:
        network=evaluate_expansion_network(state)
        for hub in network.hubs:
            tier=int(getattr(hub,"promotion_tier",3) or 3)
            if tier not in (1,2):
                continue
            planet=by_id.get(int(hub.planet_id))
            if planet is None:
                continue
            hubs.append({
                "planet_id":int(planet.id),"name":planet.name,"position":planet.position,
                "promotion_tier":tier,
                "radius_ly":100.0 if tier==1 else PRIORITY_HUB_RECON_RADIUS_LY,
            })
    except Exception:
        # A sparse/test state may not carry enough economic inputs for the
        # expansion-network scorer. Home-first survey remains safe in that
        # case and native routing never depends on this advisory enrichment.
        pass
    return hubs


def _needs_recon(planet) -> bool:
    return not planet.observed


def _probe_fleets(state):
    """Dedicated scout hulls only.  Colony, miner, and unknown ships do not explore."""
    return [
        f for f in state.fleets
        if f.owner==state.player_id and f.role=="scout"
    ]


def _early_fighter_scouts(state, known_enemy: bool):
    """A couple of idle fighters may take a short local look during the opening."""
    turn=max(0, int(getattr(state, "year", 2400) or 2400) - 2400)
    if known_enemy or turn > EARLY_FIGHTER_SCOUT_TURN:
        return []
    idle=[
        fleet for fleet in state.fleets
        if fleet.owner==state.player_id
        and fleet.destination_planet_id is None
        and fleet.role=="combat"
    ]
    home=homeworld_center(state)
    idle.sort(key=lambda fleet: (distance(fleet.position, home), int(fleet.id)))
    return idle[:EARLY_FIGHTER_SCOUT_CAP]


def _scout_hull_summary(state, probes):
    """Return scout counts and the independent singleton fleets to audit.

    Normalized fixture/live states without decoded ship counts describe a
    dedicated scout fleet as one mission-capable scout. When native counts are
    available, only a fleet containing exactly one scout hull is subject to the
    route invariant; a stacked fleet must first be split and cannot receive
    independent destinations in this M-file.
    """
    raw_profiles=((state.native or {}).get("design_profiles") or [])
    profiles=(
        list(raw_profiles.values()) if isinstance(raw_profiles,dict)
        else list(raw_profiles)
    )
    scout_slots={
        int(profile.get("design_number",-1))
        for profile in profiles
        if isinstance(profile,dict)
        and str(profile.get("role","") or "").casefold()=="scout"
        and int(profile.get("design_number",-1))>=0
    }
    total_hulls=0
    singleton=[]
    stacked=0
    for probe in probes:
        counts=list(((probe.native or {}).get("ship_count") or []))
        hulls=sum(
            max(0,int(counts[slot] or 0))
            for slot in scout_slots if 0 <= slot < len(counts)
        )
        # The adapter has not decoded a ship composition for all legacy/small
        # fixtures. A fleet already classified as a scout still represents one
        # mission-capable survey asset in that case.
        if not counts or not scout_slots:
            hulls=1
        total_hulls+=hulls
        if hulls==1:
            singleton.append(probe)
        elif hulls>1:
            stacked+=1
    return {
        "scout_hull_count":total_hulls,
        "singleton_scout_fleets":singleton,
        "stacked_scout_fleet_count":stacked,
    }


def _record_scout_productivity_invariant(
    state,
    orders,
    probes,
    unknown_all,
    *,
    assigned=None,
    cancelled_outer_routes=None,
):
    """Publish the final route-or-reason contract for singleton scouts.

    ``assigned`` is the one global reservation pool used by route construction.
    A scout may therefore be BLOCKED only after every unique reachable target
    was reserved, or after route construction/fuel safety supplied a concrete
    diagnostic. This avoids treating an unexplained idle scout as normal.
    """
    assigned={int(pid) for pid in (assigned or set())}
    cancelled={int(fid) for fid in (cancelled_outer_routes or set())}
    summary=_scout_hull_summary(state,probes)
    singletons=summary["singleton_scout_fleets"]

    cancellation_pending={
        int(order.payload.get("fleet_id"))
        for order in orders.orders
        if order.kind=="cancel_scout_route"
        and order.payload.get("fleet_id") is not None
    } | cancelled
    active_order_by_fleet={}
    active_targets=[]
    for order in orders.orders:
        if order.kind!="move_fleet" or bool(order.payload.get("deconflicted_hold")):
            continue
        fleet_id=order.payload.get("fleet_id")
        if fleet_id is None:
            continue
        mission=str(order.payload.get("mission","") or "")
        if mission not in {"scan","recon","refuel_for_scan"} and not bool(order.payload.get("route_managed")):
            continue
        active_order_by_fleet[int(fleet_id)]=order
        if mission in {"scan","recon"} and order.payload.get("destination_planet_id") is not None:
            active_targets.append(int(order.payload["destination_planet_id"]))

    policy=(state.native or {}).setdefault("scout_exploration_policy",{})
    final_audit=[]
    for probe in singletons:
        fid=int(probe.id)
        if fid in cancellation_pending:
            status="cancellation_pending"
            blocked_reason=None
        elif fid in active_order_by_fleet or probe.destination_planet_id is not None:
            status="active_route"
            blocked_reason=None
        elif not unknown_all:
            status="BLOCKED"
            blocked_reason="no unobserved worlds remain for reconnaissance"
        else:
            reachable_unreserved=any(
                int(planet.id) not in assigned
                and mission_reachable(probe,planet.position,"scan")
                for planet in unknown_all
            )
            status="BLOCKED"
            blocked_reason=(
                "reachable useful targets were reserved for other scouts; no unique target remains"
                if not reachable_unreserved else
                "route construction produced no safe native waypoint; requires route/fuel investigation"
            )
        final_audit.append({
            "fleet_id":fid,
            "status":status,
            "blocked_reason":blocked_reason,
        })

    policy.update({
        "scout_hull_count":summary["scout_hull_count"],
        "scout_fleet_count":len(probes),
        "mission_capable_singleton_scout_ids":sorted(int(probe.id) for probe in singletons),
        "singleton_scout_capacity":len(singletons),
        "stacked_scout_fleet_count":summary["stacked_scout_fleet_count"],
        "active_scout_routes":len(active_targets),
        "idle_scout_count":sum(1 for row in final_audit if row["status"]=="BLOCKED"),
        "duplicate_scout_targets":len(active_targets)-len(set(active_targets)),
        "final_audit":final_audit,
        "final_audit_violations":[
            row for row in final_audit
            if row["status"] not in {"active_route","cancellation_pending","BLOCKED"}
        ],
        "productivity_invariant_satisfied":all(
            row["status"] in {"active_route","cancellation_pending","BLOCKED"}
            for row in final_audit
        ),
    })


def _outward_unknown(state, candidates, probe):
    """One unknown world on the far side of the allied cluster.

    Home-ring clearance still runs first. After that, a single scout starts
    toward the open edge instead of lingering beside allied worlds.
    """

    allies = allied_player_ids(state)
    allied = [planet for planet in state.planets if planet.owner in allies]
    if not allied:
        return None
    center_x = sum(planet.position.x for planet in allied) / len(allied)
    center_y = sum(planet.position.y for planet in allied) / len(allied)
    reachable = [
        planet for planet in candidates
        if mission_reachable(probe, planet.position, "scan")
    ]
    if not reachable:
        return None
    return max(
        reachable,
        key=lambda planet: (
            math.hypot(planet.position.x - center_x, planet.position.y - center_y),
            -int(planet.id),
        ),
    )


def add_exploration_orders(
    state: GameState,
    orders: OrderSet,
    plan: StrategicPlan | None = None,
    memory=None,
) -> None:
    """
    Exploration is a native queued-route campaign, not a yearly nearest-target
    choice. Each scout receives up to seven fuel-safe waypoints.  Worlds inside
    400 ly of the homeworld are cleared before any route leaves that
    neighborhood.  After that backlog is gone, a fuel-safe route may extend
    the frontier.  Only scout hulls survey; up to two idle fighters may take
    a short local hop through turn 25.
    """
    probes=_probe_fleets(state)
    pending_scout_splits={
        int(fleet_id)
        for fleet_id in (state.native or {}).get("pending_scout_split_fleet_ids", [])
        if str(fleet_id).lstrip("-").isdigit()
    }
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
        "global_target_reservation_order":[
            "local_home_p1_p2_unknowns",
            "contested_border_reconnaissance",
            "frontier_targets",
            "secondary_exploration",
        ],
        # A source fleet is split now; the child fleet identities arrive only
        # in the next M-file.  It must receive no same-turn route assignment.
        "split_pending_fleet_ids":sorted(pending_scout_splits),
    }
    if not probes:
        return

    pressure=float(turn_mapping(state, "strategic_watchdog").get("exploration_pressure", 1.0))
    scout_weight=(
        (plan.objective("scout")*plan.mission("scan")) if plan else 1.0
    )*pressure

    recent_targets={int(x) for x in turn_sequence(state, "recent_scan_targets")}

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
        _record_scout_productivity_invariant(
            state,orders,probes,unknown_all,assigned=set(),
        )
        return
    home_center=homeworld_center(state)
    home_only_unknown_ids={
        int(p.id) for p in unknown_all
        if distance(home_center,p.position)<=HOME_NEIGHBORHOOD_RECON_RADIUS_LY
    }
    priority_hubs=_priority_survey_hubs(state)
    priority_hub_unknown_ids={
        int(planet.id) for planet in unknown_all
        if any(
            distance(hub["position"],planet.position)<=float(hub["radius_ly"])
            for hub in priority_hubs
        )
    }
    # Retain the historical variable name below because it now represents the
    # full local-survey invariant (home plus P1/P2 relay zones), not only the
    # home ring.  No surplus scout is allowed to reserve a deep route while
    # this set is non-empty.
    home_unknown_ids=home_only_unknown_ids | priority_hub_unknown_ids
    home_clearance_active=bool(home_unknown_ids)
    local_centers=[home_center,*[hub["position"] for hub in priority_hubs]]

    def local_target_key(planet):
        return (
            min(distance(center,planet.position) for center in local_centers),
            distance(home_center,planet.position),int(planet.id),
        )

    # A pre-existing outer waypoint used to bypass the local-backlog policy:
    # the route was preserved before a newly idle scout could be assigned one
    # of the nearby unknown worlds.  Do not continue spending years on that
    # outer route.  The writer emits the captured Type-3 cancellation only;
    # next turn, after the host has removed the route, the scout receives its
    # ordinary one-stop local survey order.
    cancelled_outer_routes=set()
    if home_clearance_active:
        planets_by_id={int(planet.id):planet for planet in state.planets}
        local_targets=[
            planet for planet in unknown_all
            if int(planet.id) in home_unknown_ids
        ]
        outer_routes=[]
        for probe in probes:
            destination=probe.destination_planet_id
            target=planets_by_id.get(int(destination)) if destination is not None else None
            if target is None or int(target.id) in home_unknown_ids:
                continue
            route_value=0.0
            if memory is not None:
                remembered=memory.scout_route(int(probe.id)) or {}
                discoveries=float(remembered.get("expected_discoveries",1) or 1)
                total_distance=float(remembered.get("total_distance",1) or 1)
                route_value=discoveries/max(1.0,total_distance)
            nearest_local=min(
                (distance(probe.position,local.position) for local in local_targets),
                default=float("inf"),
            )
            # The local repair must not tear down the whole frontier network.
            # Pick the scout that can replace a local hole most cheaply, then
            # prefer the least productive persistent route as a tie-break.
            outer_routes.append((nearest_local,route_value,int(probe.id),probe,target))
        required_local_scouts=min(len(local_targets),len(outer_routes))
        for _,_,_,probe,target in sorted(outer_routes,key=lambda row:row[:3])[:required_local_scouts]:
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
                    f"Cancel scout route to {target.name}: unexplored worlds remain in a local home/P1/P2 survey zone. "
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
    fighter_scouts=_early_fighter_scouts(state, known_enemy)
    exploration_assets=max(1,len(probes)+len(fighter_scouts))
    route_limit=min(
        MAX_QUEUED_SCOUT_WAYPOINTS,
        max(1,math.ceil(len(unknown_all)/exploration_assets)),
    )
    active_route_limit=(
        HOME_CLEARANCE_MAX_ROUTE_STOPS
        if home_clearance_active else route_limit
    )
    # When there are enough free probes to cover a sizeable local debt, assign
    # the first leg one-per-scout.  This is the global-assignment part of the
    # local survey policy: three local holes should receive three scouts, not
    # one two-stop route and a second route that consumes the third hole.
    local_singleton_assignment=bool(
        home_clearance_active
        and len(home_unknown_ids)>=3
        and len(probes)>=len(home_unknown_ids)
    )

    # Reserve one genuinely local leftover for each early combat auxiliary.
    # Dedicated probes then route around those worlds instead of consuming every
    # nearby target and leaving the auxiliary with only a distant impossible leg.
    aux_targets={}
    aux_reserved=set()
    for aux in fighter_scouts:
        nearby=[
            p for p in unknown_all
            if p.id not in aux_reserved
            and distance(aux.position,p.position)<=EARLY_FIGHTER_SCOUT_RANGE_LY
            and distance(home_center,p.position)<=HOME_NEIGHBORHOOD_RECON_RADIUS_LY
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
        "home_neighborhood_unknown_ids": sorted(home_only_unknown_ids),
        "home_neighborhood_unknown_count": len(home_only_unknown_ids),
        "unknown_within_home_radius":len(home_only_unknown_ids),
        "priority_hub_survey_zones":[
            {key:value for key,value in hub.items() if key != "position"}
            for hub in priority_hubs
        ],
        "unknown_within_priority_hub_radius":len(priority_hub_unknown_ids),
        "local_survey_unknown_ids":sorted(home_unknown_ids),
        "home_neighborhood_clearance_active": home_clearance_active,
        "home_clearance_route_stop_cap": (
            HOME_CLEARANCE_MAX_ROUTE_STOPS if home_clearance_active else None
        ),
        "required_local_scouts": min(len(home_unknown_ids),len(cancelled_outer_routes)),
        "cancelled_outer_route_fleet_ids": sorted(cancelled_outer_routes),
        "deferred_outer_persistent_route_ids": sorted(deferred_outer_route_ids),
    })

    already_moving={
        int(o.payload["fleet_id"]) for o in orders.orders
        if o.kind=="move_fleet" and o.payload.get("fleet_id") is not None
    } | pending_scout_splits

    route_diags=[]
    outward_scout_assigned=False

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
            nearby_backlog=[p for p in unassigned_unknown if int(p.id) in home_unknown_ids]
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
                            *local_target_key(planet),distance(probe.position,planet.position),
                        ),
                    )
                    # One first leg per scout explores more worlds by turn 40
                    # than one long chain that leaves the other scouts idle.
                    # Spare stops on a route cover worlds that do not have
                    # their own scout, still walking outward from home.
                    share=max(
                        1,
                        math.ceil(len(nearby_backlog)/max(1,len(probes))),
                    )
                    local_stop_cap=(
                        1 if local_singleton_assignment
                        else min(HOME_CLEARANCE_MAX_ROUTE_STOPS, share)
                    )
                    route=build_probe_route(
                        state,probe,candidates,reserved=assigned,
                        pressure=pressure,
                        max_stops=local_stop_cap,
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
                outward=None if outward_scout_assigned else _outward_unknown(state, candidates, probe)
                if outward is not None:
                    outward_scout_assigned=True
                route=build_probe_route(
                    state,probe,candidates,
                    reserved=assigned,
                    pressure=pressure,
                    max_stops=active_route_limit,
                    sector=scout_sector(state,probe),
                    first_planet_id=None if outward is None else int(outward.id),
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
                        int(target.id) in home_unknown_ids
                    ),
                    "local_survey_clearance":bool(int(target.id) in home_unknown_ids),
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

    # Early-game auxiliary reconnaissance is a capped local exception for idle
    # fighters. Dedicated scout hulls own the survey.
    for fleet in fighter_scouts:
        if int(fleet.id) in already_moving:
            continue
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
                and distance(fleet.position,p.position)<=EARLY_FIGHTER_SCOUT_RANGE_LY
                and distance(home_center,p.position)<=HOME_NEIGHBORHOOD_RECON_RADIUS_LY
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
                "short-range auxiliary reconnaissance with an idle fighter; scout hulls own the survey."
            ),
            priority=max(45,int(54*scout_weight)),
        )
        already_moving.add(fleet.id)

    _record_scout_productivity_invariant(
        state,orders,probes,unknown_all,
        assigned=assigned,cancelled_outer_routes=cancelled_outer_routes,
    )


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
        if f.role != "scout":
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
        pressure=float(turn_mapping(state, "strategic_watchdog").get("exploration_pressure", 1.0))
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

    # Deconfliction is the final exploration mutation in the agent pipeline.
    # Refresh the same audit so playtest tooling sees the routes that will
    # actually reach the native writer, not a pre-deconfliction snapshot.
    probes=_probe_fleets(state)
    unknown_all=[planet for planet in state.planets if _needs_recon(planet)]
    assigned={
        int(target_id)
        for order in orders.orders
        if order.kind=="move_fleet" and not bool(order.payload.get("deconflicted_hold"))
        for target_id in order.payload.get(
            "route_planet_ids",
            [order.payload.get("destination_planet_id")],
        )
        if target_id is not None
    }
    cancelled=(state.native or {}).get("scout_exploration_policy",{}).get(
        "cancelled_outer_route_fleet_ids",[]
    )
    _record_scout_productivity_invariant(
        state,orders,probes,unknown_all,
        assigned=assigned,cancelled_outer_routes=cancelled,
    )
