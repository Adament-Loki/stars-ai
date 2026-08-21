from __future__ import annotations

from .models import GameState, OrderSet
from .util import distance
from .colony_planner import score_colony_candidates, colonization_policy
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
                "merge_fleets":"MERGE FOR BULK TRANSPORT",
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
            ranked=[c for c in score_colony_candidates(state,fleet,plan) if c.planet_id not in assigned_targets]
            aboard=int(fleet.cargo_population or 0); at_owned=_owned_planet_under_fleet(state,fleet); owned=[p for p in state.planets if p.owner==state.player_id]
            if aboard < COLONY_LOAD_COLONISTS and at_owned is None and owned:
                home=min(owned,key=lambda p:distance(fleet.position,p.position))
                orders.add("move_fleet",{"fleet_id":fleet.id,"destination_planet_id":home.id,"warp":mission_warp(fleet,home.position,"return_for_colonists"),"mission":"return_for_colonists"},f"Empty colony ship is away from owned population; return to {home.name}.",priority=120)
                intents.append({"fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":"RETURN FOR COLONISTS","reason":f"Ship is empty and away from population; return to {home.name} before any colony mission.","destination_planet_id":home.id,"colony_candidates":[c.to_dict() for c in ranked[:8]]}); continue
            if ranked:
                intents.append({"fleet_id":fleet.id,"fleet_name":fleet.name,"role":fleet.role,"action":"HOLD / COLONY READY","reason":f"Best known viable candidate is {ranked[0].planet_name}, but no complete load+colonize operation was emitted. Hold; do not move empty or scout.","destination_planet_id":None,"colony_candidates":[c.to_dict() for c in ranked[:8]]})
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
