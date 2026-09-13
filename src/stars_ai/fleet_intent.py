from __future__ import annotations

from .models import GameState, OrderSet
from .util import distance
from .colony_planner import (
    colonization_policy,
    reserved_colony_target_ids,
    score_colony_candidates,
)
from .warp_policy import mission_warp
from .fuel_planner import mission_reachable
from .population_units import COLONY_LOAD_COLONISTS

RECON_ROLES = {"scout", "unknown"}


def _existing_fleet_order(orders: OrderSet, fleet_id: int):
    return next((
        o for o in orders.orders
        if (
            o.kind == "merge_fleets"
            and int(fleet_id) in {
                int(o.payload.get("target_fleet_id",o.payload.get("fleet_id",-1))),
                *(int(value) for value in (o.payload.get("source_fleet_ids") or [])),
            }
        ) or (
            o.kind in (
            "move_fleet", "colony_operation", "transport_population",
            "transport_minerals", "transport_unload_remainder", "remote_mine",
            "lay_minefield", "cancel_scout_route",
            )
            and int(o.payload.get("fleet_id", -1)) == int(fleet_id)
        )
    ), None)


def _nearest_unknown(state: GameState, fleet, excluded: set[int]):
    candidates = [p for p in state.planets if not p.observed and p.id not in excluded and mission_reachable(fleet,p.position,'scan')]
    if not candidates: return None
    return min(candidates, key=lambda p: distance(fleet.position, p.position))


def _owned_planet_under_fleet(state: GameState, fleet):
    owned=[p for p in state.planets if p.owner==state.player_id]
    pid=int((fleet.native or {}).get("position_object_id",-1))
    p=next((p for p in owned if p.id==pid),None)
    if p is not None: return p
    return next((p for p in owned if abs(float(p.position.x)-float(fleet.position.x)) <= 0.5 and abs(float(p.position.y)-float(fleet.position.y)) <= 0.5),None)


def _fleet_is_at_planet(fleet, planet) -> bool:
    """Use native object identity first, with a coordinate fallback."""
    native=fleet.native or {}
    if int(native.get("position_object_id", -1) or -1) == int(planet.id):
        return True
    return (
        abs(float(fleet.position.x)-float(planet.position.x)) <= 0.5
        and abs(float(fleet.position.y)-float(planet.position.y)) <= 0.5
    )


def _mining_candidates(state: GameState, fleet):
    ranked=[]
    for p in state.planets:
        if p.owner is not None or not p.observed: continue
        conc=(p.native or {}).get("mineral_concentrations")
        if not conc or len(conc)<3 or any(v is None for v in conc[:3]): continue
        mineral_score=sum(max(0,int(v)) for v in conc[:3]); travel=distance(fleet.position,p.position)
        if not mission_reachable(fleet,p.position,'reposition_for_remote_mining'): continue
        ranked.append((mineral_score-0.75*travel,p,mineral_score,travel))
    ranked.sort(key=lambda x:x[0],reverse=True)
    return ranked


def ensure_fleet_activity(state: GameState, orders: OrderSet, plan=None) -> list[dict]:
    """Every owned fleet has a purpose; cargo ships never scout merely to move."""
    intents=[]
    assigned_targets={int(o.payload["destination_planet_id"]) for o in orders.orders if "destination_planet_id" in o.payload}

    for fleet in [f for f in state.fleets if f.owner==state.player_id]:
        existing=_existing_fleet_order(orders,fleet.id)
        if existing is not None:
            mission=str(existing.payload.get("mission",""))
            action={
                "colony_operation":"LOAD + COLONIZE",
                "transport_population":"LOAD POPULATION + TRANSPORT",
                "transport_minerals":"LOAD + TRANSPORT",
                "transport_unload_remainder":"UNLOAD CARGO",
                "merge_fleets":(
                    "MERGE FOR FLEET RECOVERY"
                    if str(existing.payload.get("mission","")).lower()=="fuel_rescue_merge"
                    else "CONCENTRATE FOR BATTLE"
                    if str(existing.payload.get("mission","")).lower()=="concentrate_for_battle"
                    else "MERGE FOR BULK TRANSPORT"
                ),
                "lay_minefield":"LAY MINEFIELD / HOLD ANCHOR",
                "cancel_scout_route":"CANCEL OUTER ROUTE / LOCAL RECON NEXT TURN",
            }.get(existing.kind)
            if action is None:
                if mission=="return_for_colonists": action="RETURN FOR COLONISTS"
                elif mission=="return_for_population_export": action="RETURN TO EXPORT HUB"
                elif mission=="reposition_for_remote_mining": action="REPOSITION FOR REMOTE MINING"
                elif mission=="remote_mine": action="REMOTE MINE"
                else: action="MOVE"
            intents.append({
                "fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":action,
                "reason":existing.reason,"destination_planet_id":existing.payload.get("destination_planet_id"),
                "colony_candidates":([c.to_dict() for c in score_colony_candidates(state,fleet,plan)[:8]] if fleet.role=="colony" else []),
            })
            continue

        if (fleet.native or {}).get('fuel_blocked'):
            intents.append({'fleet_id':fleet.id,'fleet_name':fleet.name,'role':fleet.role,'action':'HOLD / FUEL BLOCKED','reason':(fleet.native or {}).get('fuel_block_reason','No safe fuel route.'),'destination_planet_id':None}); continue
        if fleet.destination_planet_id is not None:
            intents.append({"fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":"CONTINUE WAYPOINT","reason":f"Fleet already has active destination planet {fleet.destination_planet_id}; preserve current mission.","destination_planet_id":fleet.destination_planet_id}); continue
        existing_fleet_target=(fleet.native or {}).get("native_destination_fleet_id")
        if existing_fleet_target is not None:
            intents.append({"fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":"CONTINUE INTERCEPT","reason":f"Fleet already has active target-fleet waypoint {existing_fleet_target}; preserve the current interception order.","destination_planet_id":None}); continue

        if fleet.role=="colony":
            # A colony target claimed by an in-flight colony fleet is not a
            # missed launch opportunity for another hull.  Keep it out of the
            # actionable ranking and label the wait plainly below; otherwise
            # the report misleadingly calls an already-reserved world
            # "COLONY READY" every turn.
            all_candidates=score_colony_candidates(state,fleet,plan)
            reserved_claims=reserved_colony_target_ids(state)
            reserved_candidates=[
                candidate for candidate in all_candidates
                if int(candidate.planet_id) in reserved_claims
            ]
            ranked=[
                candidate for candidate in all_candidates
                if candidate.planet_id not in assigned_targets
                and int(candidate.planet_id) not in reserved_claims
            ]
            aboard=int(fleet.cargo_population or 0); at_owned=_owned_planet_under_fleet(state,fleet); owned=[p for p in state.planets if p.owner==state.player_id]
            if aboard < COLONY_LOAD_COLONISTS and at_owned is None and owned:
                home=min(owned,key=lambda p:distance(fleet.position,p.position))
                orders.add("move_fleet",{"fleet_id":fleet.id,"destination_planet_id":home.id,"warp":mission_warp(fleet,home.position,"return_for_colonists"),"mission":"return_for_colonists"},f"Empty colony ship is away from owned population; return to {home.name}.",priority=120)
                intents.append({"fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":"RETURN FOR COLONISTS","reason":f"Ship is empty and away from population; return to {home.name} before any colony mission.","destination_planet_id":home.id,"colony_candidates":[c.to_dict() for c in ranked[:8]]}); continue
            range_blocked=int(fleet.id) in set((state.native or {}).get("colony_range_blocked_fleet_ids",[]) or [])
            if range_blocked and at_owned is None:
                # A fuel tender joins only after an already-completed
                # rendezvous.  Type37 is client-captured for co-located owned
                # fleets; the merged fleet receives its return order after the
                # next M-file read-back, rather than mixing an unvalidated
                # merge-and-retarget transaction.
                fleet_capacity=float(((fleet.native or {}).get("fuel_profile") or {}).get("fuel_capacity",0) or 0)
                tenders=[
                    candidate for candidate in state.fleets
                    if candidate.owner==state.player_id
                    and candidate.role in {"freighter","scout"}
                    and candidate.destination_planet_id is None
                    and int(candidate.id)!=int(fleet.id)
                    and abs(float(candidate.position.x)-float(fleet.position.x))<=0.5
                    and abs(float(candidate.position.y)-float(fleet.position.y))<=0.5
                    and float(((candidate.native or {}).get("fuel_profile") or {}).get("fuel_capacity",0) or 0)>fleet_capacity
                    and not any(int(((candidate.native or {}).get("cargo",{}) or {}).get(key,0) or 0) for key in ("ironium","boranium","germanium","population"))
                ]
                if tenders:
                    tender=max(tenders,key=lambda candidate:float(((candidate.native or {}).get("fuel_profile") or {}).get("fuel_capacity",0) or 0))
                    orders.add(
                        "merge_fleets",
                        {
                            "fleet_id":int(tender.id),
                            "target_fleet_id":int(tender.id),
                            "source_fleet_ids":[int(fleet.id)],
                            "mission":"fuel_rescue_merge",
                            "recovery_base_required":True,
                            "rescue_colony_fleet_id":int(fleet.id),
                        },
                        (
                            f"Range recovery: merge stranded colony fleet {fleet.name} into co-located "
                            f"high-fuel tender {tender.name}; after native read-back, return the merged fleet "
                            "to the nearest reachable refuel base."
                        ),
                        priority=174,
                    )
                    intents.append({"fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":"MERGE FOR FLEET RECOVERY","reason":f"Co-located tender {tender.name} has more fuel capacity; merge before returning together to refuel.","destination_planet_id":None,"colony_candidates":[c.to_dict() for c in ranked[:8]]})
                    continue
                refuel_bases=[
                    world for world in owned
                    if bool((((world.native or {}).get("starbase_capabilities") or {}).get("can_refuel")))
                    and mission_reachable(fleet,world.position,"recovery_return")
                ]
                if refuel_bases:
                    base=min(refuel_bases,key=lambda world:distance(fleet.position,world.position))
                    orders.add("move_fleet",{"fleet_id":fleet.id,"destination_planet_id":base.id,"warp":mission_warp(fleet,base.position,"recovery_return"),"mission":"recovery_return","colony_range_recovery":True},f"Range recovery: return loaded colony fleet {fleet.name} to reachable refuel base {base.name} before selecting another colony leg.",priority=168)
                    intents.append({"fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":"RETURN FOR COLONY RECOVERY","reason":f"Range-blocked colony asset can still reach refuel base {base.name}; recover it before the next launch.","destination_planet_id":base.id,"colony_candidates":[c.to_dict() for c in ranked[:8]]})
                    continue
            if ranked:
                intents.append({"fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":("HOLD / COLONY RANGE BLOCKED" if range_blocked else "HOLD / COLONY READY"),"reason":(f"Best known viable candidate is {ranked[0].planet_name}, but no owned refuel/population launch world can safely reach it with this colony design." if range_blocked else f"Best known viable candidate is {ranked[0].planet_name}, but no complete load+colonize operation was emitted. Hold; do not move empty or scout."),"destination_planet_id":None,"colony_candidates":[c.to_dict() for c in ranked[:8]]})
            elif reserved_candidates:
                candidate=reserved_candidates[0]
                claimant=next(
                    (
                        other.name for other in state.fleets
                        if other.owner==state.player_id
                        and other.role=="colony"
                        and int(other.destination_planet_id or -1)==int(candidate.planet_id)
                    ),
                    "an in-flight colony fleet",
                )
                intents.append({"fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":"HOLD / COLONY CLAIM RESERVED","reason":f"{candidate.planet_name} is already reserved by {claimant}; hold this launch-ready hull rather than duplicate its colonization order.","destination_planet_id":None,"colony_candidates":[c.to_dict() for c in reserved_candidates[:8]]})
            else:
                policy=colonization_policy(state,plan); quality=("resource-driven universal-hab policy" if policy.normal_habitability_floor is None else f"{policy.stage} racial-habitability floor of {policy.normal_habitability_floor}%")
                intents.append({"fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":"HOLD FOR COLONY INTEL","reason":f"No known colony world meets the {quality}. Preserve the colony ship at owned population while scouts gather better options.","destination_planet_id":None,"colony_candidates":[]})
            continue

        if fleet.role=="freighter":
            intents.append({"fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":"HOLD FOR LOGISTICS","reason":"No explicit cargo route was selected. Cargo ships do not reposition or scout merely to avoid idling.","destination_planet_id":None}); continue

        if fleet.role=="miner":
            candidates=_mining_candidates(state,fleet)
            if candidates:
                score,target,mineral_score,travel=candidates[0]
                if _fleet_is_at_planet(fleet,target):
                    # Arrival with a movement task is not a productive remote
                    # miner. The captured client transaction changes the
                    # stationary current waypoint to task 3.
                    orders.add("remote_mine",{
                        "fleet_id":fleet.id,"destination_planet_id":target.id,
                        "warp":0,"mission":"remote_mine",
                        "target_mineral_score":mineral_score,
                        "native_reference":"sandbox/GAME.x2 Type5 current-waypoint task=3",
                    },f"Set Remote Mining task at {target.name}; concentration sum={mineral_score}. Client reference uses stationary WaypointChangeTask task 3.",priority=82)
                    assigned_targets.add(target.id)
                    intents.append({"fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":"REMOTE MINE","reason":f"At observed mining target {target.name}; set native Remote Mining waypoint task.","destination_planet_id":target.id})
                    continue
                orders.add("move_fleet",{"fleet_id":fleet.id,"destination_planet_id":target.id,"warp":mission_warp(fleet,target.position,"reposition_for_remote_mining"),"mission":"reposition_for_remote_mining"},f"Move remote miner to observed mining target {target.name}; concentration sum={mineral_score}, distance={travel:.1f}, score={score:.1f}.",priority=66)
                assigned_targets.add(target.id); intents.append({"fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":"REPOSITION FOR REMOTE MINING","reason":f"Observed mineral target {target.name}; concentration sum={mineral_score}, distance={travel:.1f}.","destination_planet_id":target.id})
            else:
                intents.append({"fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":"HOLD FOR MINING TARGET","reason":"No observed unowned planet with usable mineral-concentration data is known. Do not send a miner to an unknown world.","destination_planet_id":None})
            continue

        if fleet.role=="minelayer":
            waypoints=list((fleet.native or {}).get("waypoints") or [])
            if (
                int((fleet.native or {}).get("waypoint_count",len(waypoints)) or 0)==1
                and waypoints
                and int(waypoints[0].get("task",0) or 0)==6
            ):
                raw_anchor_id=waypoints[0].get("position_object")
                anchor_id=int(-1 if raw_anchor_id is None else raw_anchor_id) & 0x7FF
                intents.append({"fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":"LAY MINEFIELD / HOLD ANCHOR","reason":"Native task 6 is already active. Keep this dedicated layer stationary so each turn increases the minefield's density and radius.","destination_planet_id":anchor_id}); continue
            intents.append({"fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":"HOLD / MINE MISSION BLOCKED","reason":"No validated mine-laying mission is available; hold rather than move without a strategic objective.","destination_planet_id":None}); continue

        if fleet.role in RECON_ROLES:
            managed={int(x) for x in (state.native or {}).get("recon_route_managed_fleets",[])}
            if int(fleet.id) in managed:
                intents.append({"fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":"HOLD / NO FORWARD ROUTE","reason":"Persistent probe router found no useful one-way unknown route and no refuel detour with sufficient exploration payoff.","destination_planet_id":None}); continue
            target=_nearest_unknown(state,fleet,assigned_targets)
            if target is not None:
                mission="scan" if fleet.role=="scout" else "recon"
                orders.add("move_fleet",{"fleet_id":fleet.id,"destination_planet_id":target.id,"warp":mission_warp(fleet,target.position,mission),"mission":mission},f"{fleet.role} fleet investigates unexplored planet {target.name}.",priority=52)
                assigned_targets.add(target.id); intents.append({"fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":"RECON","reason":f"Reconnaissance mission: investigate unknown planet {target.name}.","destination_planet_id":target.id})
            else:
                intents.append({"fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":"HOLD / NO RECON TARGET","reason":"No one-way unexplored reconnaissance target is available.","destination_planet_id":None})
            continue

        if fleet.role=="combat" or fleet.combat_power>0:
            intents.append({"fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":"HOLD / DEFEND","reason":"Armed fleet has no higher-priority military mission; remain available for local defense or concentration.","destination_planet_id":None}); continue

        intents.append({"fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":"BLOCKED","reason":"No valid planner objective was generated for this fleet; investigate fleet role/state decoding.","destination_planet_id":None})
    return intents
