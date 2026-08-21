
"""Mission speed selection and active-waypoint speed maintenance."""

from __future__ import annotations
from .util import distance
from .fuel_planner import fastest_fuel_safe_warp, reconnaissance_warp

def mission_warp(fleet,target_position,mission:str,pressure:float=1.0)->int:
    """Return the fastest fuel-safe normal warp for a proposed mission leg."""
    d=distance(fleet.position,target_position); n=getattr(fleet,'native',{}) or {}; fp=n.get('fuel_profile'); flags=n.get('race_fuel_flags',{})
    if fp and fp.get('groups'):
        m=str(mission or '').lower()
        if m in ('scan','recon'):
            safe=reconnaissance_warp(
                fp,d,
                ife=bool(flags.get('ife')),
                ce=bool(flags.get('ce')),
                pressure=pressure,
            )
        else:
            safe=fastest_fuel_safe_warp(fp,d,mission,ife=bool(flags.get('ife')),ce=bool(flags.get('ce')))
        return int(safe) if safe is not None else 1
    # A synthetic/non-native state has no per-engine fuel geometry.  Its
    # ``speed`` is the usable movement ceiling, so use it rather than silently
    # applying an old role-specific Warp-7/8 cruise cap.  Warp 9 is Stars!'
    # normal fast travel; Warp 10 remains intentionally excluded as unsafe.
    return max(1,min(9,int(getattr(fleet,'speed',9) or 9)))


def _active_route_mission(fleet) -> str | None:
    """Map a decoded native waypoint task to its fuel-planning mission."""
    task=getattr(fleet,"destination_task",None)
    if task is None:
        task=(getattr(fleet,"native",{}) or {}).get("native_destination_task")
    task=int(task or 0)
    if task==3:
        # Remote mining is a stationary task. Do not turn it into a moving
        # Type-5 mutation merely because its stale target is still decoded.
        return None
    if task==2:
        return "colonize"
    if task==1:
        return "transport"
    if str(getattr(fleet,"role",""))=="scout":
        return "scan"
    existing=str(getattr(fleet,"destination_mission","") or "").lower()
    return existing if existing else "move"


def optimize_active_route_warps(state, orders) -> int:
    """Refresh each active native waypoint to its fastest fuel-safe Warp.

    The planner sees a fresh fuel level and cargo mass every turn. Existing
    waypoints must therefore be reconsidered every turn too: otherwise a fleet
    ordered at Warp 4/7 once keeps that old cruise rate until arrival. This
    emits only same-target, same-task speed changes; the native writer encodes
    these as a Type-5 waypoint update and continues to reject retargeting.
    """
    planets={int(planet.id):planet for planet in state.planets}
    acted_on={
        int(order.payload.get("fleet_id",-1))
        for order in orders.orders
        if order.kind in {
            "move_fleet","colony_operation","transport_population",
            "transport_minerals","transport_unload_remainder","remote_mine",
            "merge_fleets",
        }
    }
    updates=0
    for fleet in state.fleets:
        if fleet.owner!=state.player_id or int(fleet.id) in acted_on:
            continue
        native=fleet.native or {}
        if int(native.get("waypoint_count",0) or 0)<2:
            continue
        destination=getattr(fleet,"destination_planet_id",None)
        target=planets.get(int(destination)) if destination is not None else None
        if target is None:
            continue
        # A completed waypoint can remain visible during the current turn. It
        # needs task handling, not a meaningless movement-speed rewrite.
        if distance(fleet.position,target.position)<=0.01:
            continue
        mission=_active_route_mission(fleet)
        if mission is None:
            continue
        selected=int(mission_warp(fleet,target.position,mission))
        current=getattr(fleet,"destination_warp",None)
        if current is None:
            current=native.get("native_destination_warp")
        current=int(current or 0)
        if selected<=0 or selected==current:
            continue
        orders.add(
            "move_fleet",
            {
                "fleet_id":int(fleet.id),
                "destination_planet_id":int(target.id),
                "warp":selected,
                "mission":mission,
                "warp_reoptimization":True,
                "previous_warp":current,
            },
            (
                f"Refresh active route to {target.name}: Warp {current}->{selected} "
                f"using this turn's fuel, cargo, and mission profile."
            ),
            priority=64,
        )
        updates+=1
    if updates:
        orders.notes.append(
            f"ROUTE SPEED OPTIMIZATION: refreshed {updates} active waypoint(s) for fastest fuel-safe arrival."
        )
    return updates
