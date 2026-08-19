
from __future__ import annotations
from ..models import GameState, OrderSet
from ..persona import StrategicPlan
from ..util import distance
from ..colony_planner import score_colony_candidates
from ..warp_policy import mission_warp
from ..fuel_planner import mission_reachable
from ..objective_production import plan_objective_ship_builds
from ..cargo_planner import derive_cargo_plan
from ..planet_economy import (decode_race_economy, installation_status, estimated_operating_resources,
                              theoretical_max_population, planet_population_capacity,
                              population_capacity_fraction, projected_population_growth,
                              projected_next_population)

def _planet_under_fleet(fleet, owned):
    pid=int(fleet.native.get("position_object_id",-1))
    p=next((p for p in owned if p.id==pid),None)
    if p is not None:
        return p
    # Fallback to coordinates because position_object_id is not trustworthy
    # in every fleet-block variant. Stars! planet/fleet coordinates are integral.
    candidates=[
        p for p in owned
        if abs(float(p.position.x)-float(fleet.position.x)) <= 0.5
        and abs(float(p.position.y)-float(fleet.position.y)) <= 0.5
    ]
    return candidates[0] if candidates else None

def add_economic_orders(state: GameState, orders: OrderSet, plan: StrategicPlan | None = None) -> None:
    owned=[p for p in state.planets if p.owner==state.player_id]
    develop=plan.objective("develop") if plan else 1.0
    factory_w=plan.planet("factories") if plan else 1.0
    mine_w=plan.planet("mines") if plan else 1.0
    defense_w=plan.planet("defenses") if plan else 1.0



    race_economy=decode_race_economy(state.race)
    ship_builds=plan_objective_ship_builds(state,plan)
    shipyards=[p for p in owned if bool(((p.native or {}).get('starbase_capabilities') or {}).get('can_build_ships'))]
    primary=max(shipyards,key=lambda p:(p.population,p.factories,p.mines),default=None)
    existing=state.native.get('production_by_planet',{})

    for p in owned:
        queue=[]
        reasons=[]
        status=installation_status(p,race_economy)
        resource_est=estimated_operating_resources(p,race_economy)

        # Existing custom ships are strategic commitments. Preserve them at the
        # principal shipyard so one-turn autoplay does not erase work in progress.
        if primary is not None and p.id==primary.id:
            old=[]
            for q in existing.get(str(p.id),existing.get(p.id,[])):
                if int(q.get('item_type',0))==4 and int(q.get('count',0))>0:
                    slot=int(q.get('item_id',0))
                    dp=next(
                        (
                            d for d in state.native.get('design_profiles',[])
                            if int(d.get('design_number',-1))==slot
                        ),
                        None,
                    )
                    old.append({
                        'item':'ship_design',
                        'design_slot':slot,
                        'design_name':dp.get('name',f'Design #{slot+1}') if dp else f'Design #{slot+1}',
                        'quantity':int(q.get('count',0)),
                        'preserved_existing':True,
                    })
            queue.extend(old)
            preserved={int(x['design_slot']) for x in old}
            for req in ship_builds:
                if req.design_slot not in preserved:
                    queue.append(req.queue_item())
                    reasons.append(req.reason)

        # HARD RULE: never intentionally build more installations than the CURRENT
        # population can operate. Growth can open more headroom next year.
        if not race_economy.alternate_reality:
            factory_headroom=int(status["factory_headroom"])
            mine_headroom=int(status["mine_headroom"])
            germanium_per_factory=3 if race_economy.factory_germanium_discount else 4
            germanium_reserve=max(8,2*germanium_per_factory)
            germanium_for_factories=max(0,int(p.germanium)-germanium_reserve)
            germanium_factory_cap=germanium_for_factories//germanium_per_factory
            germanium_short=(
                factory_headroom>0
                and germanium_factory_cap < min(factory_headroom,10)
            )

            # If Germanium is the constraint, build useful mines first.
            if mine_headroom>0 and germanium_short:
                desired=max(
                    1,
                    round(
                        min(8,mine_headroom,max(1,p.population//25000))
                        * min(max(mine_w,1.25),1.75)
                    ),
                )
                qty=min(mine_headroom,desired)
                if qty>0:
                    queue.append({'item':'mine','quantity':qty,'reason':'germanium_constraint'})

            # Never queue more factories than current surface Germanium can pay
            # for after a small strategic reserve.
            if factory_headroom>0 and germanium_factory_cap>0:
                desired=max(
                    1,
                    round(
                        min(10,factory_headroom,max(1,p.population//20000))
                        * min(factory_w,1.75)
                    ),
                )
                qty=min(factory_headroom,desired,germanium_factory_cap)
                if qty>0:
                    queue.append({
                        'item':'factory',
                        'quantity':qty,
                        'germanium_cost_each':germanium_per_factory,
                        'germanium_available':int(p.germanium),
                    })

            if mine_headroom>0 and not germanium_short:
                desired=max(
                    1,
                    round(
                        min(8,mine_headroom,max(1,p.population//25000))
                        * min(mine_w,1.75)
                    ),
                )
                qty=min(mine_headroom,desired)
                if qty>0:
                    queue.append({'item':'mine','quantity':qty})

            # Defenses are not a default sink for excess resources. Only a
            # persona with an explicitly elevated defense posture spends on them;
            # otherwise capped planets prefer objective ships or research.
            if (
                defense_w>1.15
                and p.defenses<max(5,round(5*defense_w))
                and p.population>=70000
            ):
                queue.append({'item':'defense','quantity':max(1,round(defense_w))})

        current_queue=existing.get(str(p.id),existing.get(p.id,[])) or []
        current_has_standard=any(
            int(q.get('item_type',0))==2 and int(q.get('count',0))>0
            for q in current_queue
        )
        current_has_custom=any(
            int(q.get('item_type',0))==4 and int(q.get('count',0))>0
            for q in current_queue
        )

        payload={
            'planet_id':p.id,
            'queue':queue,
            'economy':{
                **status,
                'estimated_resources':resource_est['estimated_resources'],
                'operable_factories_per_10k':race_economy.operable_factories_per_10k,
                'operable_mines_per_10k':race_economy.operable_mines_per_10k,
                'factory_output_per_10':race_economy.factory_output_per_10,
                'mine_output_per_10':race_economy.mine_output_per_10,
                'germanium_surface':int(p.germanium),
                'germanium_per_factory':3 if race_economy.factory_germanium_discount else 4,
                'germanium_concentration':(p.native or {}).get('mineral_concentrations',[None,None,None])[2],
                'population_raw_hundreds':(p.native or {}).get('population_raw_hundreds'),
                'population_source_year':(p.native or {}).get('population_source_year',state.year),
                'theoretical_race_max_population':theoretical_max_population(state.race),
                'planet_population_capacity':planet_population_capacity(p,state.race),
                'population_capacity_percent':round(population_capacity_fraction(p,state.race)*100,1),
                'projected_growth':projected_population_growth(p,state.race),
                'projected_next_population':projected_next_population(p,state.race),
            },
        }

        if queue:
            if reasons:
                reason=(
                    f"{plan.persona_name + ': ' if plan else ''}objective production first: "
                    + ' | '.join(reasons)
                )
                priority=110
            else:
                reason=(
                    f"{plan.persona_name + ': ' if plan else ''}build only currently operable infrastructure: "
                    f"factories {p.factories}/{status['factory_cap']}, mines {p.mines}/{status['mine_cap']} "
                    f"at population {p.population:,}."
                )
                priority=int(60*develop)
            orders.add('set_planet_queue',payload,reason,priority=priority)
        else:
            # If Stars! is carrying a stale factory/mine queue after the population
            # cap has been reached, explicitly clear it. An empty production queue
            # allows the planet's available resources to flow to empire research.
            #
            # If no current queue exists, no native order is needed; report logic
            # still identifies the planet as RESEARCH / NO USEFUL BUILD.
            should_clear=bool(current_queue)
            if should_clear:
                payload['clear_queue']=True
                payload['research_when_idle']=True
                orders.add(
                    'set_planet_queue',
                    payload,
                    (
                        f"{plan.persona_name + ': ' if plan else ''}population currently supports only "
                        f"{status['factory_cap']} factories and {status['mine_cap']} mines; "
                        f"existing {p.factories}/{p.mines}. No higher-priority ship or useful installation "
                        "is queued, so clear planetary production and direct capacity to research."
                    ),
                    priority=92,
                )



    colony_fleets=[
        f for f in state.fleets
        if f.owner==state.player_id and f.role=="colony" and f.destination_planet_id is None
    ]
    expand=plan.objective("expand")*plan.mission("colonize") if plan else 1.0
    watchdog=(state.native or {}).get("strategic_watchdog") or {}
    expand*=float(watchdog.get("colonization_pressure",1.0))
    assigned=set()

    for fleet in colony_fleets:
        ranked=[c for c in score_colony_candidates(state,fleet) if c.planet_id not in assigned and mission_reachable(fleet,next(p.position for p in state.planets if p.id==c.planet_id),'colonize')]
        source=_planet_under_fleet(fleet,owned)
        aboard=int(fleet.cargo_population or 0)

        if not ranked:
            # Do not create a movement order just to keep a colony ship busy.
            if aboard<25000 and source is None and owned:
                home=min(owned,key=lambda p:distance(fleet.position,p.position))
                orders.add(
                    "move_fleet",
                    {
                        "fleet_id":fleet.id,
                        "destination_planet_id":home.id,
                        "warp":mission_warp(fleet,home.position,"return_for_colonists"),
                        "mission":"return_for_colonists",
                        "cargo_population_before":aboard,
                    },
                    f"{plan.persona_name + ': ' if plan else ''}return empty colony ship to {home.name}; "
                    "wait there until scouts identify a viable colony.",
                    priority=int(118*expand),
                )
            continue

        best=ranked[0]
        loaded=aboard>=25000
        can_load=source is not None and source.population>=75000 and aboard<25000

        if loaded or can_load:
            assigned.add(best.planet_id)
            alts="; ".join(f"{c.planet_name}={c.score:.1f}" for c in ranked[1:4]) or "none"
            orders.add(
                "colony_operation",
                {
                    "fleet_id":fleet.id,
                    "source_planet_id":source.id if can_load else None,
                    "destination_planet_id":best.planet_id,
                    "warp":mission_warp(
                        fleet,
                        next(p.position for p in state.planets if p.id==best.planet_id),
                        "colonize",
                    ),
                    "load_25k_population":bool(can_load),
                    "population":25000 if can_load else aboard,
                    "mission":"colonize",
                    "cargo_population_before":aboard,
                    "source_population":source.population if source else None,
                    "load_decision":"emit_25k_load" if can_load else "already_loaded",
                },
                (
                    f"{plan.persona_name + ': ' if plan else ''}colonize {best.planet_name}; "
                    f"score={best.score:.1f}; {best.explanation}. Alternatives: {alts}. "
                    +("Load validated 25k colonists first." if can_load else "Use colonists already aboard.")
                ),
                priority=int(125*expand),
            )
            continue

        # A viable target exists, but the ship cannot safely execute it yet.
        # Empty ships away from population return home. Ships already at owned
        # population simply hold; fleet_intent records that purposeful wait.
        if aboard<25000 and source is None and owned:
            home=min(owned,key=lambda p:distance(fleet.position,p.position))
            orders.add(
                "move_fleet",
                {
                    "fleet_id":fleet.id,
                    "destination_planet_id":home.id,
                    "warp":mission_warp(fleet,home.position,"return_for_colonists"),
                    "mission":"return_for_colonists",
                    "cargo_population_before":aboard,
                    "desired_colony_planet_id":best.planet_id,
                    "desired_colony_score":best.score,
                },
                f"{plan.persona_name + ': ' if plan else ''}best colony is {best.planet_name}, "
                f"but the ship is empty; return to {home.name} for population first.",
                priority=int(120*expand),
            )


    logistics=plan.objective("logistics")*plan.mission("transport") if plan else 1.0
    freighters=[
        f for f in state.fleets
        if f.owner==state.player_id and f.role=="freighter" and f.destination_planet_id is None
    ]

    for fleet in freighters:
        here=_planet_under_fleet(fleet,owned)
        cargo=(fleet.native or {}).get("cargo",{})
        ci=int(cargo.get("ironium",0) or 0)
        cb=int(cargo.get("boranium",0) or 0)
        cg=int(cargo.get("germanium",0) or 0)

        # The destination waypoint itself now performs the complete delivery:
        # Unload All I/B/G/Population + Load Optimal Fuel.
        #
        # If cargo is unexpectedly still aboard at an owned planet, recover by
        # reissuing that same validated policy before assigning another route.
        if here is not None and (ci>0 or cb>0 or cg>0 or int(cargo.get("population",0) or 0)>0):
            orders.add(
                "transport_unload_remainder",
                {
                    "fleet_id":fleet.id,
                    "destination_planet_id":here.id,
                    "warp":int((fleet.native or {}).get("observed_warp",1) or 1),
                    "mission":"transport_unload_all",
                    "cargo_before":{
                        "ironium":ci,
                        "boranium":cb,
                        "germanium":cg,
                        "population":int(cargo.get("population",0) or 0),
                    },
                    "unload":{
                        "ironium":"all",
                        "boranium":"all",
                        "germanium":"all",
                        "population":"all",
                    },
                    "fuel":"load_optimal",
                },
                f"{plan.persona_name + ': ' if plan else ''}finish delivery at {here.name} before another route; "
                f"cargo remaining I/B/G={ci}/{cb}/{cg}. Use validated Unload All cargo + Load Optimal fuel.",
                priority=145,
            )
            continue

        source=here
        if source is None:
            continue


        candidates=[]
        for target in owned:
            if target.id==source.id or not mission_reachable(fleet,target.position,"transport"):
                continue
            cargo_plan=derive_cargo_plan(source,target,fleet,race_economy,orders)
            if cargo_plan is None:
                continue

            travel=distance(fleet.position,target.position)
            # Germanium gets higher strategic weight because factory growth
            # depends on it; useful total payload and short travel break ties.
            route_score=(
                3.0*cargo_plan.germanium
                +cargo_plan.ironium
                +cargo_plan.boranium
                -0.25*travel
            )
            candidates.append((route_score,target,cargo_plan,travel))

        if not candidates:
            continue

        route_score,target,cargo_plan,travel=max(candidates,key=lambda x:x[0])
        load=cargo_plan.as_load()

        orders.add(
            "transport_minerals",
            {
                "fleet_id":fleet.id,
                "source_planet_id":source.id,
                "destination_planet_id":target.id,
                "warp":mission_warp(fleet,target.position,"transport"),
                "load":load,
                "load_total":cargo_plan.total,
                "cargo_capacity":cargo_plan.capacity,
                "cargo_capacity_confidence":(fleet.native or {}).get(
                    "cargo_capacity_confidence","unknown"
                ),
                "unload":{
                    "ironium":"all",
                    "boranium":"all",
                    "germanium":"all",
                    "population":"all",
                },
                "fuel":"load_optimal",
                "cargo_plan":cargo_plan.to_dict(),
            },
            f"{plan.persona_name + ': ' if plan else ''}dynamic shipment "
            f"{source.name}->{target.name}: I/B/G="
            f"{load['ironium']}/{load['boranium']}/{load['germanium']}kT "
            f"({cargo_plan.total}/{cargo_plan.capacity}kT conservative capacity). "
            + " ".join(cargo_plan.rationale)
            + " Destination Transport task unloads all cargo and loads optimal fuel.",
            priority=int((88+min(25,cargo_plan.germanium//4))*logistics),
        )

