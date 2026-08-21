
from __future__ import annotations
from ..models import GameState, OrderSet
from ..persona import StrategicPlan
from ..util import distance
from ..colony_planner import score_colony_candidates, reserved_colony_target_ids
from ..warp_policy import mission_warp
from ..fuel_planner import mission_reachable, mission_reachable_with_planned_cargo
from ..objective_production import BuildRequest, plan_objective_ship_builds
from ..cargo_planner import derive_cargo_plan
from ..starbase_planner import (
    plan_support_base_builds,
    plan_support_base_material_demands,
    STARBASE_QUEUE_SLOT_OFFSET,
)
from ..expansion_network import evaluate_expansion_network
from ..planet_economy import (decode_race_economy, installation_status, estimated_operating_resources,
                              estimated_mineral_output, mineral_surface_stock,
                              working_mineral_reserve, factory_germanium_floor,
                              is_economic_core, theoretical_max_population,
                              planet_population_capacity, population_capacity_fraction,
                              projected_population_growth, projected_next_population)
from ..population_units import (
    COLONY_LOAD_COLONISTS,
    COLONY_LOAD_KT,
    cargo_kt_from_colonists,
    colony_source_reserve_for_turn,
)
from ..terraforming import evaluate_terraforming
from ..research_planner import ResearchDecision
from ..planetary_scanners import (
    PLANETARY_SCANNER_QUEUE_ITEM,
    best_penetrating_planetary_scanner,
    planetary_scanner_sites,
)
from ..shared_transport import schedule_shared_transport_orders

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


def _colony_source_reserve(state: GameState) -> int:
    """Population retained before emitting validated 25 kT load orders."""
    turn = max(0, int(state.year) - 2400)
    return colony_source_reserve_for_turn(turn)


def _distribute_ship_builds(shipyards, requests, promotion_by_planet):
    """Spread strategic builds across operational yards.

    A ship build request is an empire requirement, not an instruction to make
    one world carry the whole queue.  Giving each ready yard a share produces
    colony, scout, freight, and defense hulls concurrently.  Core/promotion
    hubs are still assigned first, so the fastest and best supplied yard gets
    the first share without silently idling every other shipyard.
    """
    ordered=sorted(
        shipyards,
        key=lambda p:(
            -int(getattr(promotion_by_planet.get(int(p.id)),"promotion_tier",3) or 3),
            int(p.population or 0),int(p.factories or 0),int(p.mines or 0),-int(p.id),
        ),
        reverse=True,
    )
    assigned={int(p.id):[] for p in ordered}
    if not ordered:
        return assigned
    for request in requests:
        remaining=max(0,int(request.quantity))
        lane_index=0
        shares={int(world.id):0 for world in ordered}
        # One ship to each lane first gives the greatest immediate strategic
        # throughput.  Extra copies cycle from the strongest yard outward.
        while remaining:
            world=ordered[lane_index % len(ordered)]
            shares[int(world.id)]+=1
            lane_index+=1
            remaining-=1
        for world in ordered:
            quantity=shares[int(world.id)]
            if quantity:
                assigned[int(world.id)].append(BuildRequest(
                    request.role,request.design_slot,request.design_name,quantity,
                    request.priority,request.reason,
                ))
    return assigned


def _prioritize_economic_infrastructure(queue, *, opening_growth:bool=False):
    """Put operable mines and factories ahead of discretionary projects.

    A queue is a commitment to an order, not a claim that all of its mineral
    bills are already funded.  When the planner detects an I/B/G shortfall,
    allowing a long scout, colony, or base queue to remain first causes the
    planet to spend its early production on work it cannot complete while its
    mineral income remains flat.  Installations must therefore be first even
    during a research sprint; a research contributor that is deliberately
    cleared later still receives its explicit empty/research queue.
    """
    mines=[item for item in queue if item.get("item")=="mine"]
    factories=[item for item in queue if item.get("item")=="factory"]
    if not mines and not factories:
        return queue
    other=[
        item for item in queue
        if item.get("item") not in {"mine","factory"}
    ]
    if opening_growth:
        # Early playtest evidence showed a preserved scout queue hiding a new
        # colony build behind fourteen probes.  Exploration now has enough
        # momentum; mines/factories first, then colony hulls and the freighters
        # that sustain them, is the necessary opening production sequence.
        project_order={
            "colony":0,
            "freighter":1,
            "miner":2,
            "scout":3,
            "combat":4,
        }
        other=[
            item
            for _,item in sorted(
                enumerate(other),
                key=lambda row:(
                    project_order.get(str(row[1].get("role") or ""), 5)
                    if row[1].get("item")=="ship_design" else 5,
                    row[0],
                ),
            )
        ]
    return [*mines,*factories,*other]

def add_economic_orders(
    state: GameState,
    orders: OrderSet,
    plan: StrategicPlan | None = None,
    research_decision: ResearchDecision | None = None,
) -> None:
    owned=[p for p in state.planets if p.owner==state.player_id]
    develop=plan.objective("develop") if plan else 1.0
    defense_w=plan.planet("defenses") if plan else 1.0



    race_economy=decode_race_economy(state.race)
    promotion_network=evaluate_expansion_network(state)
    promotion_by_planet={int(h.planet_id):h for h in promotion_network.hubs}
    penetrating_scanner=best_penetrating_planetary_scanner(state)
    scanner_sites=set(planetary_scanner_sites(state,penetrating_scanner,limit=2))
    state.native["planetary_sensor_plan"]={
        "capability":penetrating_scanner,
        "target_planet_ids":sorted(scanner_sites),
        "policy":"Deploy up to two researched penetrating planetary scanners on mature core/frontier hubs; existing M-file scanners are retained.",
    }
    ship_builds=plan_objective_ship_builds(state,plan)
    # A strategic base is not allowed to block its world's mines/factories
    # until the exact remaining hull/component bill plus working reserve is on
    # the ground.  The blocked demand remains visible to freight routing below.
    support_base_material_demands={
        int(demand.planet_id): demand
        for demand in plan_support_base_material_demands(state,plan)
    }
    state.native["support_base_material_demands"]=[
        demand.to_dict() for demand in support_base_material_demands.values()
    ]
    support_base_builds={
        request.planet_id:request
        for request in plan_support_base_builds(state,plan)
        if bool(
            support_base_material_demands.get(int(request.planet_id))
            and support_base_material_demands[int(request.planet_id)].ready
        )
    }
    shipyards=[p for p in owned if bool(((p.native or {}).get('starbase_capabilities') or {}).get('can_build_ships'))]
    ship_builds_by_planet=_distribute_ship_builds(
        shipyards,ship_builds,promotion_by_planet,
    )
    existing=state.native.get('production_by_planet',{})
    research_posture=str(
        getattr(research_decision,"posture","") or ""
    ).rsplit(".",1)[-1].upper()
    key_research_sprint=bool(
        research_decision is not None
        and research_posture in {"SPRINT","MILITARY_EMERGENCY"}
        and int(research_decision.allocation_percent or 0)>=25
    )
    selected_research_contributors=set(
        int(x) for x in (
            research_decision.contributor_planet_ids if research_decision is not None else ()
        )
    )
    # Routine research never displaces baseline economic development.  Only a
    # named high-commitment sprint (or an active military emergency) may do so.
    # This is shared empire logic, deliberately independent of AI persona.
    research_contributors=(
        selected_research_contributors if key_research_sprint else set()
    )
    protected_research=set(
        int(x) for x in (
            research_decision.protected_production_planet_ids if research_decision is not None else ()
        )
    )

    # The empirically validated leftover-only flag protects critical shipyards
    # during a 25% sprint without clearing their colony/scout/defense work.
    if research_decision is not None and research_decision.allocation_percent == 25:
        for planet_id in sorted(protected_research):
            planet=next((p for p in owned if int(p.id)==planet_id),None)
            if planet is None:
                continue
            queue=existing.get(str(planet_id),existing.get(planet_id,[])) or []
            shipyard=bool(((planet.native or {}).get('starbase_capabilities') or {}).get('can_build_ships'))
            has_custom=any(int(q.get('item_type',0) or 0)==4 and int(q.get('count',0) or 0)>0 for q in queue)
            if shipyard or has_custom:
                orders.add(
                    'set_planet_research_mode',
                    {'planet_id':planet_id,'leftover_only':True,'capability_id':research_decision.capability_id},
                    f"Protect {planet.name} production during the {research_decision.capability_name} sprint; contribute leftover resources only.",
                    priority=146,
                )

    for p in owned:
        queue=[]
        reasons=[]
        strategic_priority=0
        status=installation_status(p,race_economy)
        resource_est=estimated_operating_resources(p,race_economy)
        promotion=promotion_by_planet.get(int(p.id))
        promotion_tier=int(getattr(promotion,"promotion_tier",3) or 3)
        promotion_rank=getattr(promotion,"promotion_rank",None)
        promotion_label={0:"HW",1:"P1",2:"P2"}.get(promotion_tier,"LOCAL")
        promotion_priority={0:132,1:126,2:120}.get(promotion_tier,110)

        base_demand=support_base_material_demands.get(int(p.id))
        base_request=support_base_builds.get(p.id)
        if base_request is not None:
            queue.append(base_request.queue_item())
            reasons.append(base_request.reason)
            strategic_priority=max(strategic_priority,base_request.priority)
        elif base_demand is not None:
            # Keep the normal development queue active while minerals are
            # concentrated.  Leaving the stalled starbase at queue position 1
            # was the direct cause of idle, under-mined hub worlds.
            reasons.append(base_demand.reason)

        # Existing custom ships are strategic commitments. Preserve them at
        # every operational shipyard so parallel production never erases work
        # that was previously assigned to a secondary yard.
        if int(p.id) in ship_builds_by_planet:
            old=[]
            for q in existing.get(str(p.id),existing.get(p.id,[])):
                if (
                    int(q.get('item_type',0))==4
                    and 0 <= int(q.get('item_id',0)) < STARBASE_QUEUE_SLOT_OFFSET
                    and int(q.get('count',0))>0
                ):
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
            preserved={int(x['design_slot']):x for x in old}
            for req in ship_builds_by_planet[int(p.id)]:
                if req.design_slot in preserved:
                    item=preserved[req.design_slot]
                    item['quantity']=int(item.get('quantity',0))+int(req.quantity)
                    item['role']=req.role
                    item['objective_top_up']=int(req.quantity)
                    reasons.append(
                        f"{req.reason} Add {req.quantity} to the preserved "
                        f"{req.design_name} queue."
                    )
                    strategic_priority=max(strategic_priority,req.priority)
                else:
                    queue.append(req.queue_item())
                    reasons.append(req.reason)
                    strategic_priority=max(strategic_priority,req.priority)

        # HARD RULE: never intentionally build more installations than the CURRENT
        # population can operate. Growth can open more headroom next year.
        infrastructure=[]
        # Promotion has a deliberate resource sequence. The homeworld remains
        # the primary core; selected P1 worlds receive the same reserve and
        # installation-first treatment so they can become true relay cores.
        # P2s receive accelerated development only after their P1 parent is
        # operational (the expansion network withholds their active lane until
        # then), preventing the AI from overextending into the third tier.
        is_core=is_economic_core(p) or promotion_tier <= 1
        promoted_p2=promotion_tier == 2 and bool(getattr(promotion,"parent_exporter_id",None))
        mineral_stock=mineral_surface_stock(p)
        mineral_output=estimated_mineral_output(p,race_economy)
        if not race_economy.alternate_reality:
            factory_headroom=int(status["factory_headroom"])
            mine_headroom=int(status["mine_headroom"])
            germanium_per_factory=3 if race_economy.factory_germanium_discount else 4
            reserve_before_infrastructure=working_mineral_reserve(
                p,race_economy,queue,is_core=is_core,
            )
            mineral_deficits={
                mineral:max(0,int(reserve_before_infrastructure[mineral])-mineral_stock[mineral])
                for mineral in ("ironium","boranium","germanium")
            }
            germanium_reserve=factory_germanium_floor(
                p,race_economy,is_core=is_core,
            )
            germanium_for_factories=max(0,int(p.germanium)-germanium_reserve)
            germanium_factory_cap=germanium_for_factories//germanium_per_factory
            if promotion_tier == 0:
                factory_batch=min(factory_headroom,25); mine_batch=min(mine_headroom,25)
            elif promotion_tier == 1:
                factory_batch=min(factory_headroom,20); mine_batch=min(mine_headroom,20)
            elif is_core:
                factory_batch=min(factory_headroom,25); mine_batch=min(mine_headroom,25)
            elif promoted_p2:
                factory_batch=min(factory_headroom,14); mine_batch=min(mine_headroom,12)
            else:
                factory_batch=min(factory_headroom,10); mine_batch=min(mine_headroom,8)
            # A mine field begins producing in groups of ten.  Avoid issuing a
            # token mine queue that cannot produce any minerals next year when
            # the planet has the population/resources to complete the group.
            operated_mines=int(mineral_output["operated_mines"])
            if operated_mines<10 and mine_headroom>0:
                mine_batch=min(
                    mine_headroom,
                    max(mine_batch,10-operated_mines),
                )
            germanium_short=(
                factory_headroom>0
                and germanium_factory_cap < factory_batch
            )
            output_shortfall=any(
                amount is not None and int(amount)<(12 if is_core else 8 if promoted_p2 else 4)
                for amount in mineral_output["estimated_mineral_output"].values()
            )
            needs_mining=bool(
                mine_headroom>0
                and (
                    is_core
                    or promoted_p2
                    or germanium_short
                    or any(mineral_deficits.values())
                    or output_shortfall
                )
            )

            # Mine capacity comes first whenever a core needs stock or
            # production is constrained.  This grows all three minerals rather
            # than reacting only after Germanium has already run out.
            if needs_mining and mine_batch>0:
                mine_reason=(
                    "core_mineral_reserve"
                    if is_core and any(mineral_deficits.values())
                    else "core_mine_capacity" if is_core
                    else "mineral_reserve_or_output"
                )
                infrastructure.append({
                    'item':'mine',
                    'quantity':mine_batch,
                    'reason':mine_reason,
                    'mineral_deficits':mineral_deficits,
                })
                reasons.append(
                    f"baseline mineral capacity: {mine_batch} mines for I/B/G reserve "
                    f"deficits {mineral_deficits['ironium']}/"
                    f"{mineral_deficits['boranium']}/"
                    f"{mineral_deficits['germanium']}kT"
                )

            # Never queue more factories than current surface Germanium can pay
            # for after the appropriate working floor.  Core worlds keep a
            # larger stock so they can continue ship/base production.
            if factory_headroom>0 and germanium_factory_cap>0:
                qty=min(factory_batch,germanium_factory_cap)
                if qty>0:
                    infrastructure.append({
                        'item':'factory',
                        'quantity':qty,
                        'germanium_cost_each':germanium_per_factory,
                        'germanium_available':int(p.germanium),
                        'germanium_floor':germanium_reserve,
                    })
                    reasons.append(
                        f"baseline factory capacity: {qty} factories while retaining "
                        f"{germanium_reserve}kT Germanium"
                    )

            # Non-core colonies still develop toward their current operating
            # cap even when their stock report is incomplete.
            if mine_headroom>0 and not needs_mining:
                infrastructure.append({
                    'item':'mine','quantity':mine_batch,'reason':'mine_capacity'
                })
                reasons.append(f"baseline mine capacity: {mine_batch} mines")

            # Defenses are not a default sink for excess resources. Only a
            # persona with an explicitly elevated defense posture spends on them;
            # otherwise capped planets prefer objective ships or research.
            if (
                defense_w>1.15
                and p.defenses<max(5,round(5*defense_w))
                and p.population>=70000
            ):
                infrastructure.append({'item':'defense','quantity':max(1,round(defense_w))})

        # Mines and factories are the universal economic foundation: optional
        # ships, bases, and terraforming never jump ahead of currently operable
        # infrastructure.  Research allocation changes whether a world is
        # cleared for research; it never reverses an under-mined world's
        # production order.
        if infrastructure:
            queue[0:0]=infrastructure
            if promotion_tier <= 1:
                reasons.insert(
                    0,
                    f"{promotion_label} promotion rank {promotion_rank or 1}: "
                    "installations precede optional local projects",
                )
            strategic_priority=max(strategic_priority,promotion_priority)

        # Terraforming improves a mature world, but it is never the reason an
        # immature world postpones the mines and factories that pay for it.
        terraforming=evaluate_terraforming(state,p)
        if terraforming.tech_steps>0 and terraforming.tech_gain>0:
            queue.append({
                'item':'max_terraform',
                'quantity':1,
                'current_habitability':terraforming.current_habitability,
                'target_habitability':terraforming.tech_habitability,
                'terraform_steps':terraforming.tech_steps,
                'resource_cost_per_step':terraforming.resource_cost_per_step,
            })
            reasons.append(
                f"terraform {p.name} from {terraforming.current_habitability}% toward "
                f"{terraforming.tech_habitability}% ({terraforming.tech_steps} available steps) after economic infrastructure"
            )
            strategic_priority=max(strategic_priority,108)

        current_queue=existing.get(str(p.id),existing.get(p.id,[])) or []
        current_has_standard=any(
            int(q.get('item_type',0))==2 and int(q.get('count',0))>0
            for q in current_queue
        )
        current_has_custom=any(
            int(q.get('item_type',0))==4 and int(q.get('count',0))>0
            for q in current_queue
        )

        is_research_contributor=int(p.id) in research_contributors
        generated_critical=any(q.get('item') in ('ship_design','starbase_design') for q in queue)
        if is_research_contributor and (current_has_custom or generated_critical):
            is_research_contributor=False
            orders.notes.append(
                f"RESEARCH PROTECTION: {p.name} was not cleared for the "
                f"{research_decision.capability_name if research_decision else 'research'} sprint "
                "because a critical ship/starbase build is present."
            )
        elif is_research_contributor:
            queue=[]
            reasons=[
                f"selected mature contributor for the {research_decision.capability_name} "
                f"{research_decision.posture.lower()}"
            ]
            strategic_priority=max(strategic_priority,145)

        if int(p.id) in scanner_sites:
            scanner_name=str((penetrating_scanner or {}).get("name", "penetrating planetary scanner"))
            scanner_range=int((penetrating_scanner or {}).get("range", 0) or 0)
            queue.append({
                "item":PLANETARY_SCANNER_QUEUE_ITEM,
                "quantity":1,
                "scanner_name":scanner_name,
                "scanner_range":scanner_range,
                "penetrating":True,
                "standard_item_id":27,
                "native_status":"ENABLED_UNTRUSTED_STANDARD_QUEUE_ITEM",
            })
            reasons.append(
                f"deploy researched {scanner_name} planetary sensor ({scanner_range} ly normal / "
                f"{scanner_range // 2} ly penetrating range) for border and planet intelligence"
            )
            strategic_priority=max(strategic_priority,118)

        # Keep the precedence explicit at the final queue assembly point.  It
        # protects against future queue additions above (or a preserved custom
        # ship build) accidentally placing an unaffordable ship ahead of the
        # mine/factory work that restores its mineral supply.
        queue=_prioritize_economic_infrastructure(
            queue,
            opening_growth=max(0,int(state.year)-2400)<10,
        )

        mineral_reserve=working_mineral_reserve(
            p,race_economy,queue,is_core=is_core,
        )
        mineral_deficits={
            mineral:max(0,int(mineral_reserve[mineral])-mineral_stock[mineral])
            for mineral in ("ironium","boranium","germanium")
        }
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
                'economic_core':is_core,
                'promotion':{
                    'tier':promotion_tier,
                    'label':promotion_label,
                    'rank':promotion_rank,
                    'parent_id':getattr(promotion,"promotion_parent_id",None),
                    'parent_operational':bool(getattr(promotion,"parent_exporter_id",None)),
                    'economic_value':getattr(promotion,"economic_value",None),
                    'strategic_value':getattr(promotion,"strategic_value",None),
                    'overall_value':getattr(promotion,"overall_value",None),
                    'development_priority':promotion_priority,
                },
                'mineral_surface_stock':mineral_stock,
                'mineral_reserve':mineral_reserve,
                'mineral_reserve_deficits':mineral_deficits,
                'support_base_material_demand':(
                    base_demand.to_dict() if base_demand is not None else None
                ),
                'estimated_mineral_output':mineral_output['estimated_mineral_output'],
                'mineral_concentrations':mineral_output['concentrations'],
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
                priority=max(promotion_priority,110,strategic_priority)
            else:
                reason=(
                    f"{plan.persona_name + ': ' if plan else ''}build only currently operable infrastructure: "
                    f"factories {p.factories}/{status['factory_cap']}, mines {p.mines}/{status['mine_cap']} "
                    f"at population {p.population:,}."
                )
                priority=int(60*develop)
            orders.add('set_planet_queue',payload,reason,priority=priority)
        else:
            # Emit an explicit native queue decision even for a previously empty
            # world.  A deliberate empty/research queue is not an accidental
            # idle planet and makes every planet's turn intent auditable.
            payload['clear_queue']=True
            payload['research_when_idle']=True
            orders.add(
                'set_planet_queue',
                payload,
                (
                    f"{plan.persona_name + ': ' if plan else ''}"
                    + (
                        f"selected contributor to the {research_decision.capability_name} sprint; "
                        "hold an explicit empty queue and direct capacity to the named unlock."
                        if is_research_contributor and research_decision is not None
                        else (
                            f"population currently supports only {status['factory_cap']} factories and "
                            f"{status['mine_cap']} mines; existing {p.factories}/{p.mines}. No higher-priority "
                            "operable production exists, so hold an explicit research queue."
                        )
                    )
                ),
                priority=145 if is_research_contributor else 92,
            )



    colony_fleets=[
        f for f in state.fleets
        if f.owner==state.player_id and f.role=="colony" and f.destination_planet_id is None
    ]
    expand=plan.objective("expand")*plan.mission("colonize") if plan else 1.0
    watchdog=(state.native or {}).get("strategic_watchdog") or {}
    expand*=float(watchdog.get("colonization_pressure",1.0))
    assigned=reserved_colony_target_ids(state)
    source_reserve=_colony_source_reserve(state)
    planned_population_loads={}
    minimum_launch_population=COLONY_LOAD_COLONISTS+source_reserve
    loading_worlds=[
        p for p in owned
        if int(p.population or 0)>=minimum_launch_population
    ]
    refuel_worlds=[
        p for p in owned
        if bool((((p.native or {}).get("starbase_capabilities") or {}).get("can_refuel")))
    ]

    def staging_world(fleet):
        choices=loading_worlds or refuel_worlds
        if not choices:
            return None
        # Population is the primary launch constraint; an operational base is
        # the tie-breaker because it supplies a full, predictable fuel tank.
        return max(
            choices,
            key=lambda p:(
                int(p.population or 0),
                bool((((p.native or {}).get("starbase_capabilities") or {}).get("can_refuel"))),
                -distance(fleet.position,p.position),
            ),
        )

    for fleet in colony_fleets:
        source=_planet_under_fleet(fleet,owned)
        aboard=int(fleet.cargo_population or 0)
        population_to_load=max(0,COLONY_LOAD_COLONISTS-aboard)
        population_to_load_kt=cargo_kt_from_colonists(population_to_load)
        raw_ranked=[
            c for c in score_colony_candidates(state,fleet,plan)
            if c.planet_id not in assigned
        ]
        ranked=[]
        for candidate in raw_ranked:
            target=next(p for p in state.planets if p.id==candidate.planet_id)
            reachable=(
                mission_reachable(fleet,target.position,'colonize')
                if aboard>=COLONY_LOAD_COLONISTS
                else mission_reachable_with_planned_cargo(
                    fleet,target.position,'colonize',{"population":population_to_load_kt}
                )
            )
            if reachable:
                ranked.append(candidate)

        if not ranked:
            # Do not create a movement order just to keep a colony ship busy.
            # If useful colony intelligence exists, however, stage an empty
            # hull at the best population/refuel world so it is launch-ready.
            home=staging_world(fleet) if raw_ranked and aboard<COLONY_LOAD_COLONISTS else None
            if home is not None and (source is None or source.id!=home.id):
                orders.add(
                    "move_fleet",
                    {
                        "fleet_id":fleet.id,
                        "destination_planet_id":home.id,
                        "warp":mission_warp(fleet,home.position,"return_for_colonists"),
                        "mission":"return_for_colonists",
                        "cargo_population_before":aboard,
                    },
                    f"{plan.persona_name + ': ' if plan else ''}stage empty colony ship at {home.name}; "
                    f"known viable claims exist, but none is fuel-safe with the planned "
                    f"{COLONY_LOAD_KT} kT / {COLONY_LOAD_COLONISTS:,}-colonist load. "
                    f"Prepare at the best population/refuel hub while support expands.",
                    priority=int(118*expand),
                )
            continue

        best=ranked[0]
        loaded=aboard>=COLONY_LOAD_COLONISTS
        can_load=(
            source is not None
            and (
                int(source.population or 0)
                -int(planned_population_loads.get(int(source.id),0))
            )>=source_reserve+population_to_load
            and aboard<COLONY_LOAD_COLONISTS
        )

        if loaded or can_load:
            assigned.add(best.planet_id)
            already_committed=(
                int(planned_population_loads.get(int(source.id),0))
                if source is not None else 0
            )
            if can_load and source is not None:
                planned_population_loads[int(source.id)]=already_committed+population_to_load
            source_after=(
                int(source.population)-already_committed-population_to_load
                if can_load and source is not None else None
            )
            alts="; ".join(f"{c.planet_name}={c.score:.1f}" for c in ranked[1:4]) or "none"
            orders.add(
                "colony_operation",
                {
                    "fleet_id":fleet.id,
                    "source_planet_id":source.id if can_load else None,
                    "destination_planet_id":best.planet_id,
                    "target_habitability":best.habitability,
                    "target_current_habitability":best.current_habitability,
                    "target_current_tech_terraform_habitability":best.tech_terraform_habitability,
                    "target_eventual_terraform_habitability":best.eventual_terraform_habitability,
                    "target_terraform_steps":best.terraform_steps,
                    "target_distance_from_homeworld":round(best.distance_from_homeworld,2),
                    "colonization_stage":best.colonization_stage,
                    "habitability_floor":best.habitability_floor,
                    "selection_basis":best.selection_basis,
                    "warp":mission_warp(
                        fleet,
                        next(p.position for p in state.planets if p.id==best.planet_id),
                        "colonize",
                    ),
                    "load_25kt_population":bool(can_load),
                    "native_load_order_kt":COLONY_LOAD_KT if can_load else 0,
                    "load_population_kt":population_to_load_kt if can_load else 0,
                    "population_loaded":population_to_load if can_load else 0,
                    "population":aboard+population_to_load if can_load else aboard,
                    "mission":"colonize",
                    "cargo_population_before":aboard,
                    "source_population":source.population if source else None,
                    "source_population_after_load":(
                        source_after
                    ),
                    "source_population_reserve":source_reserve if can_load else None,
                    "minimum_launch_population":(
                        source_reserve+population_to_load if can_load else None
                    ),
                    "load_decision":"emit_25kt_load" if can_load else "already_loaded",
                },
                (
                    f"{plan.persona_name + ': ' if plan else ''}colonize {best.planet_name}; "
                    f"score={best.score:.1f}; {best.explanation}. Alternatives: {alts}. "
                    +(
                        f"Load {COLONY_LOAD_KT} kT ({COLONY_LOAD_COLONISTS:,} colonists) first; "
                        f"expect {population_to_load:,} colonists to transfer and "
                        f"{source_after:,} to remain on {source.name} after this turn's planned loads."
                        if can_load and source is not None else "Use colonists already aboard."
                    )
                ),
                priority=int(125*expand),
            )
            continue

        # A viable target exists, but the ship cannot safely execute it yet.
        # Empty ships away from population return home. Ships already at owned
        # population simply hold; fleet_intent records that purposeful wait.
        home=staging_world(fleet) if aboard<COLONY_LOAD_COLONISTS else None
        if home is not None and (source is None or source.id!=home.id):
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
                f"but the ship is empty or its current world cannot supply "
                f"{COLONY_LOAD_KT} kT ({COLONY_LOAD_COLONISTS:,} colonists) while retaining "
                f"the {source_reserve:,}-colonist reserve; stage at {home.name} for population first.",
                priority=int(120*expand),
            )


    # Population and mineral freight share one fleet-level ranked schedule.
    # The scheduler consumes the just-created production queues so a selected
    # P1/P2/tactical destination gets one full manifest, not competing orders
    # from independent population and mineral passes.
    schedule_shared_transport_orders(
        state,
        orders,
        plan,
        support_base_material_demands=support_base_material_demands,
    )
