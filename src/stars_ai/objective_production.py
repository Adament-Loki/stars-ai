from __future__ import annotations
from dataclasses import dataclass
import math

from .fuel_planner import best_range_ly,has_ife,ENGINE_DATA
from .population_units import (
    COLONY_LOAD_COLONISTS,
    COLONY_LOAD_KT,
    colony_source_reserve_for_turn,
)
from .colony_planner import colony_planet_is_eligible, colonization_policy
from .expansion_network import evaluate_expansion_network
from .logistics_capacity import evaluate_logistics_capacity, POPULATION_PULSE_KT
from .scout_policy import enemy_contact_summary, custom_scout_missions
from .territorial_defense import assess_territorial_defense


@dataclass
class BuildRequest:
    role:str
    design_slot:int
    design_name:str
    quantity:int
    priority:int
    reason:str

    def queue_item(self):
        return {
            'item':'ship_design','design_slot':self.design_slot,'design_name':self.design_name,
            'quantity':self.quantity,'role':self.role,
        }


def _designs(state):
    return {int(d['design_number']):d for d in state.native.get('design_profiles',[]) if not d.get('is_starbase',False)}


def _current(state):
    out={}
    for f in state.fleets:
        if f.owner!=state.player_id: continue
        for i,n in enumerate((f.native or {}).get('ship_count',[])):
            if n: out[i]=out.get(i,0)+int(n)
    return out


def _queued(state):
    out={}
    for qs in state.native.get('production_by_planet',{}).values():
        for q in qs:
            if int(q.get('item_type',0))==4 and 0<=int(q.get('item_id',0))<16:
                slot=int(q.get('item_id',0)); out[slot]=out.get(slot,0)+int(q.get('count',0))
    return out


def _role_count(ds,counts,role):
    return sum(n for s,n in counts.items() if ds.get(s,{}).get('role')==role)


def _design_generation(design)->int:
    """Native design turn, with zero for old/imported profiles without it."""
    return max(0,int(design.get('turn_designed',0) or 0))


def _combat_component_score(design)->int:
    """A stable tie-breaker when two combat designs share a generation."""
    score=0
    for component in design.get('components',[]) or []:
        category=int(component.get('category',0) or 0)
        item_id=int(component.get('item_id',0) or 0)
        count=max(0,int(component.get('count',0) or 0))
        if category in (16,32,64):       # beam, torpedo, bomb
            score+=count*(item_id+1)*10
        elif category in (8,2048):       # shield, armor
            score+=count*(item_id+1)*4
    return score


def _selected_role_gap(ds,counts,role,selected,desired,role_assets)->tuple[int,str]:
    """Return the build gap for the selected design, not merely its role.

    When an upgraded hull arrives, old ships must not satisfy the new design's
    production target.  The AI builds a current generation alongside the old
    fleet; it does not scrap working ships just to modernize.
    """
    selected_slot=int(selected['design_number'])
    selected_assets=int(counts.get(selected_slot,0) or 0)
    selected_generation=_design_generation(selected)
    older_assets=sum(
        int(counts.get(slot,0) or 0)
        for slot,design in ds.items()
        if design.get('role')==role
        and _design_generation(design)<selected_generation
    )
    if selected_generation>0 and older_assets>0:
        return max(0,int(desired)-selected_assets),(
            f"modernization target: newest {role} generation T{selected_generation} has "
            f"{selected_assets} live/queued; {older_assets} older hull(s) do not satisfy its target"
        )
    return max(0,int(desired)-int(role_assets)),(
        f"role target: {role_assets} live/queued {role} hull(s) satisfy current generation demand"
    )


def _design_free_cruise(d)->int:
    eid=d.get('engine_id')
    if eid not in ENGINE_DATA: return 0
    table=ENGINE_DATA[eid][2]; out=0
    for w in range(1,min(9,len(table)-1)+1):
        if int(table[w])==0: out=w
    return out


def _freighter_profiles(state):
    return [d for d in _designs(state).values() if d.get('role')=='freighter']


def _pick_freighter_for_population(state):
    """Prefer compact long-range 20k-pulse carriers over bulk freighters."""
    ds=[d for d in _freighter_profiles(state) if int(d.get('cargo_capacity',0) or 0)>=POPULATION_PULSE_KT]
    if not ds:
        return None
    compact=[d for d in ds if int(d.get('cargo_capacity',0) or 0)<1000]
    pool=compact or ds
    ife=has_ife(state.race)
    return max(pool,key=lambda d:(
        _design_generation(d),
        _design_free_cruise(d),
        best_range_ly(d,7,ife),
        float(d.get('fuel_capacity',0) or 0)/max(1,int(d.get('dry_mass',1) or 1)),
        -abs(int(d.get('cargo_capacity',0) or 0)-250),
        -int(d.get('dry_mass',999999) or 999999),
    ))


def _pick_freighter_for_bulk(state):
    """Industrial concentration wants maximum useful mineral capacity."""
    ds=_freighter_profiles(state)
    if not ds:
        return None
    ife=has_ife(state.race)
    return max(ds,key=lambda d:(
        _design_generation(d),
        int(d.get('cargo_capacity',0) or 0),
        best_range_ly(d,7,ife),
        int(d.get('fuel_capacity',0) or 0),
        -int(d.get('dry_mass',999999) or 999999),
    ))


def _pick(state,role):
    ds=[d for d in _designs(state).values() if d.get('role')==role]
    if not ds:
        return None
    if role=='scout':
        ife=has_ife(state.race)
        return max(ds,key=lambda d:(
            _design_generation(d),
            _design_free_cruise(d),best_range_ly(d,7,ife),best_range_ly(d,6,ife),
            d.get('fuel_capacity',0),-d.get('dry_mass',999999),
        ))
    if role=='colony':
        ife=has_ife(state.race)
        return max(ds,key=lambda d:(
            _design_generation(d),best_range_ly(d,7,ife),d.get('fuel_capacity',0),
            -d.get('dry_mass',999999),
        ))
    if role=='freighter':
        return _pick_freighter_for_population(state)
    if role=='miner':
        return max(ds,key=lambda d:(
            _design_generation(d),d.get('fuel_capacity',0)/max(1,d.get('dry_mass',1)),
            -d.get('dry_mass',0),
        ))
    if role=='combat':
        return max(ds,key=lambda d:(
            _design_generation(d),_combat_component_score(d),int(d.get('hull_id',0) or 0),
            int(d.get('armor',0) or 0),-int(d.get('dry_mass',999999) or 999999),
        ))
    return max(ds,key=lambda d:(_design_generation(d),int(d.get('hull_id',0) or 0)))


def _desired_scout_force(state,plan,current_scout_assets:int)->tuple[int,str]:
    watchdog=(state.native or {}).get('strategic_watchdog') or {}; milestone=watchdog.get('milestone') or {}
    unknown=sum(1 for p in state.planets if not p.observed)
    contact=enemy_contact_summary(state)
    custom_missions=custom_scout_missions(state)
    if contact["enemy_contact"]:
        # Contact changes where scouts fly; it must not end expansion.  We
        # retain a real border screen while continuing to map uncontested
        # space.  This is deliberately a much larger force than the old
        # contact-limited two-scout policy.
        required=min(6,len(custom_missions))
        coverage=math.ceil(unknown/8) if unknown>0 else 0
        desired=min(24,max(6 if unknown>0 else 0,required,coverage))
        purposes=", ".join(str(m["id"]) for m in custom_missions[:6]) or "open-frontier coverage"
        return desired,(
            "foreign contact is established; maintain an expansion-race scout screen for "
            f"uncontested exploration and border intelligence [{purposes}]. "
            f"Target force={desired}; exploration continues with persistent contact-aware coverage."
        )
    if unknown<=0: return 0,"galaxy fully observed"
    turn=max(0,int(state.year)-2400); deadline=int(milestone.get('deadline_turn',turn+10))
    explored=int(watchdog.get('explored_count',sum(1 for p in state.planets if p.observed)))
    optimal=int(milestone.get('explored_optimal',min(len(state.planets),explored+20)))
    turns_left=max(1,deadline-turn); gap=max(0,optimal-explored); required_rate=gap/turns_left
    recent=float(watchdog.get('discoveries_last_5_turns',0))/5.0; live=max(1,int(current_scout_assets))
    productivity=min(1.6,max(.55,recent/live if recent>0 else .75))
    throughput_force=math.ceil(required_rate/productivity) if gap>0 else 0
    # Broad early reconnaissance makes new colony and mineral claims visible
    # soon enough to exploit them.  The minimum is intentionally aggressive:
    # an empire should field a meaningful screen before the first milestone
    # sees it fall behind.
    persona_floor=7 if (plan and plan.objective('scout')>1.15) else 6
    desired=max(persona_floor,throughput_force); pressure=float(watchdog.get('exploration_pressure',1.0))
    if pressure>=1.75: desired+=6
    elif pressure>=1.45: desired+=4
    elif pressure>=1.20: desired+=2
    # The milestone is a minimum completion obligation.  Abundant unexplored
    # space is its own opportunity, so density can lift the program beyond the
    # deadline calculation instead of treating that calculation as a ceiling.
    opportunity_force=math.ceil(unknown/8)
    desired=min(24,max(6,int(desired),opportunity_force)); desired=min(desired,unknown)
    return desired,(f"exploration milestone needs {gap} more known worlds by T{deadline} (~{required_rate:.2f}/turn); "
                    f"measured scout productivity ~{productivity:.2f} discoveries/scout-turn; target scout force={desired}")


def _desired_colony_force(state,viable,plan)->tuple[int,str]:
    if not viable:
        policy=colonization_policy(state,plan)
        floor=("resource-driven universal habitability" if policy.normal_habitability_floor is None else f"race-adjusted habitability floor {policy.normal_habitability_floor}%")
        return 0,f"no known {policy.stage} claims meeting {floor}"
    turn=max(0,int(state.year)-2400); owned=[p for p in state.planets if p.owner==state.player_id]
    source_reserve=colony_source_reserve_for_turn(turn)
    export_packets=sum(max(0,(int(p.population or 0)-source_reserve)//COLONY_LOAD_COLONISTS) for p in owned)
    loaded=sum(1 for f in state.fleets if f.owner==state.player_id and f.role=='colony' and int(f.cargo_population or 0)>=COLONY_LOAD_COLONISTS)
    watchdog=(state.native or {}).get('strategic_watchdog') or {}; pressure=float(watchdog.get('colonization_pressure',1.0))
    milestone=watchdog.get('milestone') or {}; deadline=int(milestone.get('deadline_turn',turn+10) or turn+10)
    new_colonies=int(watchdog.get('new_colonies',0) or 0); optimal=int(milestone.get('new_colonies_optimal',new_colonies+4) or (new_colonies+4))
    turns_left=max(1,deadline-turn); settlement_gap=max(0,optimal-new_colonies); required_rate=settlement_gap/turns_left
    throughput_force=math.ceil(required_rate*6.0) if settlement_gap else 0
    # Expansion is a race.  These are practical simultaneous settlement
    # targets, rather than a limit of one ship per newly observed claim.
    concurrency_cap=15 if turn<=5 else 24 if turn<=15 else 32
    if pressure>=1.75: concurrency_cap+=10
    elif pressure>=1.45: concurrency_cap+=6
    # Keep at most one speculative empty colony hull when no source can
    # currently supply even one validated 25-kT colonization packet. Once
    # population export exists, allow a small build-ahead buffer so ships can
    # arrive at breeder worlds just before the next packet is ready.
    if export_packets<=0 and loaded<=0:
        supported=1
    else:
        supported=max(8,loaded+min(export_packets,concurrency_cap)+12)
    base_pipeline=8 if turn<=10 else 12
    # A completed milestone is not a reason to idle hull capacity.  Where
    # viable claims and source population exist, keep enough colonizers in the
    # pipeline to exploit all supported concurrent opportunities.
    opportunity_force=loaded+min(export_packets,concurrency_cap)+12
    desired=max(base_pipeline,throughput_force,opportunity_force)
    desired=min(len(viable),concurrency_cap,supported,desired)
    reason=(f"{len(viable)} phase-eligible unowned claims under {colonization_policy(state,plan).stage}; {export_packets} exportable colony population packets "
            f"({COLONY_LOAD_KT} kT / {COLONY_LOAD_COLONISTS:,} colonists each); source reserve={source_reserve:,}; "
            f"milestone needs {settlement_gap} more colonies by T{deadline} (~{required_rate:.2f}/turn); {loaded} colony ships already loaded; "
            f"target concurrent colony force={desired}")
    return int(desired),reason


def _freighter_asset_counts(ds, counts):
    population=0
    bulk=0
    total=0
    for slot,n in counts.items():
        d=ds.get(slot,{})
        if d.get('role')!='freighter':
            continue
        total+=int(n)
        cargo=int(d.get('cargo_capacity',0) or 0)
        if cargo>=1000:
            bulk+=int(n)
        elif cargo>=POPULATION_PULSE_KT:
            population+=int(n)
    return population,bulk,total


def _desired_freighter_forces(state,viable_claim_count:int=0)->tuple[int,int,str,object]:
    logistics=evaluate_logistics_capacity(state)
    # Logistics needs should lead expansion rather than merely react after
    # colonies and outer hubs have already stalled.  These extra carriers are
    # a floor from visible viable claims; actual lane and mineral demand can
    # still raise either requirement further.
    expansion_transport_floor=(
        min(8,max(2,math.ceil(int(viable_claim_count)/4)))
        if viable_claim_count else 0
    )
    desired_population=max(
        int(logistics.desired_population_freighters),expansion_transport_floor,
    )
    desired_bulk=max(
        int(logistics.desired_bulk_freighters),
        # Compact carriers can use spare capacity for the first one to three
        # claims.  A dedicated bulk hull becomes an expansion floor only once
        # the visible program is large enough to justify its cost.
        math.ceil(int(viable_claim_count)/4) if viable_claim_count>=4 else 0,
    )
    reason=(
        f"population lanes={logistics.population_lane_count}, sustainable 20k pulse rate="
        f"{logistics.sustainable_population_pulses_per_turn:.2f}/turn, average flown round-trip="
        f"{logistics.average_population_round_trip_turns:.1f} turns -> desired compact population transports="
        f"{desired_population} (lane model={logistics.desired_population_freighters}, visible expansion claims={viable_claim_count}); "
        f"bulk transferable minerals={logistics.bulk_transferable_kt} kT -> desired bulk freighters={desired_bulk} "
        f"(lane model={logistics.desired_bulk_freighters}); "
        f"active shipyard builds={logistics.active_shipyard_build_count}; live freighters={logistics.live_freighter_count}, "
        f"population-loaded={logistics.population_committed_freighters}, "
        f"currently free for industrial freight={logistics.industrial_freighters_available}"
    )
    return (
        desired_population,desired_bulk,reason,logistics,
    )


def plan_objective_ship_builds(state,plan=None):
    ds=_designs(state)
    if not ds: return []
    cur=_current(state); que=_queued(state); counts={k:cur.get(k,0)+que.get(k,0) for k in set(cur)|set(que)}; req=[]
    viable=[p for p in state.planets if colony_planet_is_eligible(state,p,plan)]

    colony_assets=_role_count(ds,counts,'colony')
    active_claims=[int(f.destination_planet_id) for f in state.fleets if f.owner==state.player_id and f.role=='colony' and f.destination_planet_id is not None]
    duplicate_commitments=max(0,len(active_claims)-len(set(active_claims))); effective_colony_assets=max(0,colony_assets-duplicate_commitments)
    desired_colonies,colony_reason=_desired_colony_force(state,viable,plan)
    if d:=_pick(state,'colony'):
        gap,generation_reason=_selected_role_gap(
            ds,counts,'colony',d,desired_colonies,effective_colony_assets,
        )
        if gap:
            pressure=float(((state.native or {}).get('strategic_watchdog') or {}).get('colonization_pressure',1.0))
            priority=200 if pressure>=1.45 else 182
            req.append(BuildRequest('colony',int(d['design_number']),d['name'],min(8,gap),priority,
                f"{colony_reason}; available/queued={colony_assets}; effective distinct-claim pipeline={effective_colony_assets}; duplicate active commitments={duplicate_commitments}; {generation_reason}."))

    unknown=sum(1 for p in state.planets if not p.observed); scout_assets=_role_count(ds,counts,'scout')
    scout_contact=enemy_contact_summary(state)
    scout_custom_missions=custom_scout_missions(state)
    desired_scouts,scout_reason=_desired_scout_force(state,plan,scout_assets)
    state.native["scout_build_policy"]={
        **scout_contact,
        "classic_exploration_enabled":True,
        "contact_limited_scout_building":bool(scout_contact["enemy_contact"]),
        "custom_missions":scout_custom_missions,
    }
    scout_build_authorized=bool(unknown)
    if scout_build_authorized and (d:=_pick(state,'scout')):
        scout_gap,generation_reason=_selected_role_gap(ds,counts,'scout',d,desired_scouts,scout_assets)
        if not scout_gap:
            generation_reason=""
    else:
        scout_gap=0
        generation_reason=""
    if scout_gap and scout_build_authorized and (d:=_pick(state,'scout')):
        pressure=float(((state.native or {}).get('strategic_watchdog') or {}).get('exploration_pressure',1.0))
        priority=196 if pressure>=1.75 else 186 if pressure>=1.45 else 172 if pressure>=1.20 else 160
        if scout_contact["enemy_contact"] and scout_custom_missions:
            priority=max(priority,max(int(m.get("priority",110) or 110) for m in scout_custom_missions))
        req.append(BuildRequest('scout',int(d['design_number']),d['name'],min(6,scout_gap),priority,
            f"{unknown} worlds remain unknown; {scout_reason}; available/queued={scout_assets}. "
            f"Existing scout choice favors the newest mission-capable design and efficient Warp-7 range; {generation_reason}."))

    # Population freight and industrial bulk freight are separate jobs. Opening
    # population capacity is deliberately handled by a small aggressively cycled
    # Privateer/Medium-Freighter-class fleet; Large Freighters are added only
    # when bulk mineral concentration at shipyards justifies them.
    population_assets,_bulk_assets,total_freighter_assets=_freighter_asset_counts(ds,counts)
    desired_population,desired_bulk,freighter_reason,logistics=_desired_freighter_forces(
        state,len(viable),
    )

    population_gap=max(0,desired_population-population_assets)
    if population_gap and (d:=_pick_freighter_for_population(state)):
        req.append(BuildRequest(
            'freighter',int(d['design_number']),d['name'],min(4,population_gap),174,
            f"{freighter_reason}; compact population transports available/queued={population_assets}; "
            f"selected {d['name']} cargo={int(d.get('cargo_capacity',0) or 0)} kT, "
            "sized for repeated 20,000-colonist pulses rather than maximum hull size."
        ))

    # Compact hulls can perform mineral runs too.  Reserve every currently
    # population-loaded hull, plus the compact fleet needed to sustain the
    # active population lanes, before deciding whether outer-base mineral work
    # has a carrier.  This is what prevents an all-colonist transport pool from
    # silently blocking onion-layer starbase construction.
    population_reserved=max(
        int(logistics.population_committed_freighters),
        min(population_assets,desired_population),
    )
    industrial_assets=max(0,total_freighter_assets-population_reserved)
    bulk_gap=max(0,desired_bulk-industrial_assets)
    bulk_design=_pick_freighter_for_bulk(state)
    if bulk_gap and bulk_design is not None and int(bulk_design.get('cargo_capacity',0) or 0)>=POPULATION_PULSE_KT:
        req.append(BuildRequest(
            'freighter',int(bulk_design['design_number']),bulk_design['name'],min(4,bulk_gap),
            176 if population_reserved>=total_freighter_assets else 166,
            f"{freighter_reason}; industrial freight available/queued after reserving "
            f"{population_reserved} population carrier(s)={industrial_assets}; selected "
            f"{bulk_design['name']} cargo={int(bulk_design.get('cargo_capacity',0) or 0)} kT for major "
            "mineral concentration, outer-hub starbase loads, and fleet-construction logistics."
        ))

    mt=[]
    for p in state.planets:
        c=(p.native or {}).get('mineral_concentrations')
        if p.owner is None and p.observed and c and len(c)>=3 and all(v is not None for v in c[:3]) and sum(max(0,int(v)) for v in c[:3])>=150:
            mt.append(p)
    desired_miners=min(3,max(1,math.ceil(len(mt)/2))) if mt else 0; miner_assets=_role_count(ds,counts,'miner'); miner_gap=max(0,desired_miners-miner_assets)
    if miner_gap and (d:=_pick(state,'miner')):
        req.append(BuildRequest('miner',int(d['design_number']),d['name'],min(2,miner_gap),124,
            f"{len(mt)} strong observed remote-mining targets; desired miners={desired_miners}, available/queued={miner_assets}."))

    # Minefields are territorial claims, not a default peace-time sink. Build
    # minelayers only after a non-friendly armed or transport fleet enters a
    # perceived zone around an owned world. The military planner uses these
    # same records to deploy the completed hulls and place patrols.
    territorial = assess_territorial_defense(state, plan)
    state.native["territorial_defense"] = territorial.to_dict()
    minelayer_assets = _role_count(ds, counts, 'minelayer')
    minelayer_gap = max(0, int(territorial.desired_minelayers) - minelayer_assets)
    if minelayer_gap and (d := _pick(state, 'minelayer')):
        req.append(BuildRequest(
            'minelayer', int(d['design_number']), d['name'], min(2, minelayer_gap), 178,
            f"{territorial.reason} Minelayers available/queued={minelayer_assets}; "
            f"desired territorial minefield force={territorial.desired_minelayers}."
        ))

    # Escort is a mission assigned to the shared combat architecture, not a
    # separate design family.  Once another empire is observed, retain a small
    # real combat force instead of endlessly designing Frigate variants that
    # never reach a production queue.
    military_contact=enemy_contact_summary(state)
    combat_assets=_role_count(ds,counts,'combat')
    owned_count=sum(1 for planet in state.planets if planet.owner==state.player_id)
    baseline_combat=(min(24,max(8,math.ceil(owned_count/2))) if military_contact['enemy_contact'] else 0)
    desired_combat=max(
        baseline_combat,
        min(24, 4 + 2 * int(territorial.desired_patrols)) if territorial.needs_response else 0,
    )
    if d:=_pick(state,'combat'):
        combat_gap,generation_reason=_selected_role_gap(
            ds,counts,'combat',d,desired_combat,combat_assets,
        )
    else:
        combat_gap=0
        generation_reason=""
    if combat_gap and (d:=_pick(state,'combat')):
        req.append(BuildRequest(
            'combat',int(d['design_number']),d['name'],min(6,combat_gap),170,
            f"foreign contact is established (fleets={military_contact['foreign_fleet_count']}, "
            f"planets={military_contact['foreign_planet_count']}); shared combat/escort hulls "
            f"available or queued={combat_assets}, desired defensive force={desired_combat}; "
            f"territorial patrol objectives={territorial.desired_patrols}; {generation_reason}."
        ))

    req.sort(key=lambda r:r.priority,reverse=True)
    watchdog=(state.native or {}).get('strategic_watchdog') or {}
    high_pressure=(float(watchdog.get('exploration_pressure',1.0))>=1.45 or float(watchdog.get('colonization_pressure',1.0))>=1.45)
    # This is the empire-wide strategic order budget.  Construction is spread
    # across every operational shipyard below, so a larger budget increases
    # actual output rather than merely making one queue longer.
    budget=20 if high_pressure else 16
    out=[]
    for r in req:
        r.quantity=min(r.quantity,budget)
        if r.quantity<=0: break
        out.append(r); budget-=r.quantity
        if budget<=0: break
    return out
