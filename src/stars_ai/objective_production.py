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
            _design_free_cruise(d),best_range_ly(d,7,ife),best_range_ly(d,6,ife),
            d.get('fuel_capacity',0),-d.get('dry_mass',999999),
        ))
    if role=='colony':
        ife=has_ife(state.race)
        return max(ds,key=lambda d:(best_range_ly(d,7,ife),d.get('fuel_capacity',0),-d.get('dry_mass',999999)))
    if role=='freighter':
        return _pick_freighter_for_population(state)
    if role=='miner':
        return max(ds,key=lambda d:(d.get('fuel_capacity',0)/max(1,d.get('dry_mass',1)),-d.get('dry_mass',0)))
    return ds[0]


def _desired_scout_force(state,plan,current_scout_assets:int)->tuple[int,str]:
    watchdog=(state.native or {}).get('strategic_watchdog') or {}; milestone=watchdog.get('milestone') or {}
    unknown=sum(1 for p in state.planets if not p.observed)
    if unknown<=0: return 0,"galaxy fully observed"
    turn=max(0,int(state.year)-2400); deadline=int(milestone.get('deadline_turn',turn+10))
    explored=int(watchdog.get('explored_count',sum(1 for p in state.planets if p.observed)))
    optimal=int(milestone.get('explored_optimal',min(len(state.planets),explored+20)))
    turns_left=max(1,deadline-turn); gap=max(0,optimal-explored); required_rate=gap/turns_left
    recent=float(watchdog.get('discoveries_last_5_turns',0))/5.0; live=max(1,int(current_scout_assets))
    productivity=min(1.6,max(.55,recent/live if recent>0 else .75))
    throughput_force=math.ceil(required_rate/productivity) if gap>0 else 0
    persona_floor=3 if (plan and plan.objective('scout')>1.15) else 2
    desired=max(persona_floor,throughput_force); pressure=float(watchdog.get('exploration_pressure',1.0))
    if pressure>=1.75: desired+=2
    elif pressure>=1.45: desired+=1
    desired=min(12,max(2,int(desired))); desired=min(desired,unknown)
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
    throughput_force=math.ceil(required_rate*4.0) if settlement_gap else 0
    concurrency_cap=5 if turn<=5 else 7 if turn<=15 else 10
    if pressure>=1.75: concurrency_cap+=2
    elif pressure>=1.45: concurrency_cap+=1
    # Keep at most one speculative empty colony hull when no source can
    # currently supply even one validated 25-kT colonization packet. Once
    # population export exists, allow a small build-ahead buffer so ships can
    # arrive at breeder worlds just before the next packet is ready.
    if export_packets<=0 and loaded<=0:
        supported=1
    else:
        supported=max(2,loaded+min(export_packets,concurrency_cap)+2)
    base_pipeline=3 if turn<=10 else 4
    desired=max(base_pipeline,throughput_force); desired=min(len(viable),concurrency_cap,supported,desired)
    reason=(f"{len(viable)} phase-eligible unowned claims under {colonization_policy(state,plan).stage}; {export_packets} exportable colony population packets "
            f"({COLONY_LOAD_KT} kT / {COLONY_LOAD_COLONISTS:,} colonists each); source reserve={source_reserve:,}; "
            f"milestone needs {settlement_gap} more colonies by T{deadline} (~{required_rate:.2f}/turn); {loaded} colony ships already loaded; "
            f"target concurrent colony force={desired}")
    return int(desired),reason


def _freighter_asset_counts(ds, counts):
    population=0
    bulk=0
    for slot,n in counts.items():
        d=ds.get(slot,{})
        if d.get('role')!='freighter':
            continue
        cargo=int(d.get('cargo_capacity',0) or 0)
        if cargo>=1000:
            bulk+=int(n)
        elif cargo>=POPULATION_PULSE_KT:
            population+=int(n)
    return population,bulk


def _desired_freighter_forces(state)->tuple[int,int,str]:
    logistics=evaluate_logistics_capacity(state)
    reason=(
        f"population lanes={logistics.population_lane_count}, sustainable 20k pulse rate="
        f"{logistics.sustainable_population_pulses_per_turn:.2f}/turn, average flown round-trip="
        f"{logistics.average_population_round_trip_turns:.1f} turns -> desired compact population transports="
        f"{logistics.desired_population_freighters}; bulk transferable minerals={logistics.bulk_transferable_kt} kT, "
        f"active shipyard builds={logistics.active_shipyard_build_count} -> desired bulk freighters="
        f"{logistics.desired_bulk_freighters}"
    )
    return int(logistics.desired_population_freighters),int(logistics.desired_bulk_freighters),reason


def plan_objective_ship_builds(state,plan=None):
    ds=_designs(state)
    if not ds: return []
    cur=_current(state); que=_queued(state); counts={k:cur.get(k,0)+que.get(k,0) for k in set(cur)|set(que)}; req=[]
    viable=[p for p in state.planets if colony_planet_is_eligible(state,p,plan)]

    colony_assets=_role_count(ds,counts,'colony')
    active_claims=[int(f.destination_planet_id) for f in state.fleets if f.owner==state.player_id and f.role=='colony' and f.destination_planet_id is not None]
    duplicate_commitments=max(0,len(active_claims)-len(set(active_claims))); effective_colony_assets=max(0,colony_assets-duplicate_commitments)
    desired_colonies,colony_reason=_desired_colony_force(state,viable,plan); gap=max(0,desired_colonies-effective_colony_assets)
    if gap and (d:=_pick(state,'colony')):
        req.append(BuildRequest('colony',int(d['design_number']),d['name'],min(4,gap),130,
            f"{colony_reason}; available/queued={colony_assets}; effective distinct-claim pipeline={effective_colony_assets}; duplicate active commitments={duplicate_commitments}."))

    unknown=sum(1 for p in state.planets if not p.observed); scout_assets=_role_count(ds,counts,'scout')
    desired_scouts,scout_reason=_desired_scout_force(state,plan,scout_assets); scout_gap=max(0,desired_scouts-scout_assets)
    if scout_gap and unknown and (d:=_pick(state,'scout')):
        pressure=float(((state.native or {}).get('strategic_watchdog') or {}).get('exploration_pressure',1.0))
        priority=145 if pressure>=1.75 else 135 if pressure>=1.45 else 118 if pressure>=1.20 else 105
        req.append(BuildRequest('scout',int(d['design_number']),d['name'],min(3,scout_gap),priority,
            f"{unknown} worlds remain unknown; {scout_reason}; available/queued={scout_assets}. Existing scout choice favors free cruise then efficient Warp-7 range."))

    # Population freight and industrial bulk freight are separate jobs. Opening
    # population capacity is deliberately handled by a small aggressively cycled
    # Privateer/Medium-Freighter-class fleet; Large Freighters are added only
    # when bulk mineral concentration at shipyards justifies them.
    population_assets,bulk_assets=_freighter_asset_counts(ds,counts)
    desired_population,desired_bulk,freighter_reason=_desired_freighter_forces(state)

    population_gap=max(0,desired_population-population_assets)
    if population_gap and (d:=_pick_freighter_for_population(state)):
        req.append(BuildRequest(
            'freighter',int(d['design_number']),d['name'],min(2,population_gap),132,
            f"{freighter_reason}; compact population transports available/queued={population_assets}; "
            f"selected {d['name']} cargo={int(d.get('cargo_capacity',0) or 0)} kT, "
            "sized for repeated 20,000-colonist pulses rather than maximum hull size."
        ))

    bulk_gap=max(0,desired_bulk-bulk_assets)
    bulk_design=_pick_freighter_for_bulk(state)
    if bulk_gap and bulk_design is not None and int(bulk_design.get('cargo_capacity',0) or 0)>=1000:
        req.append(BuildRequest(
            'freighter',int(bulk_design['design_number']),bulk_design['name'],min(2,bulk_gap),126,
            f"{freighter_reason}; bulk freighters available/queued={bulk_assets}; selected "
            f"{bulk_design['name']} cargo={int(bulk_design.get('cargo_capacity',0) or 0)} kT for major "
            "mineral concentration and fleet-construction logistics."
        ))

    mt=[]
    for p in state.planets:
        c=(p.native or {}).get('mineral_concentrations')
        if p.owner is None and p.observed and c and len(c)>=3 and all(v is not None for v in c[:3]) and sum(max(0,int(v)) for v in c[:3])>=150:
            mt.append(p)
    desired_miners=min(3,max(1,math.ceil(len(mt)/2))) if mt else 0; miner_assets=_role_count(ds,counts,'miner'); miner_gap=max(0,desired_miners-miner_assets)
    if miner_gap and (d:=_pick(state,'miner')):
        req.append(BuildRequest('miner',int(d['design_number']),d['name'],1,85,
            f"{len(mt)} strong observed remote-mining targets; desired miners={desired_miners}, available/queued={miner_assets}."))

    req.sort(key=lambda r:r.priority,reverse=True)
    watchdog=(state.native or {}).get('strategic_watchdog') or {}
    high_pressure=(float(watchdog.get('exploration_pressure',1.0))>=1.45 or float(watchdog.get('colonization_pressure',1.0))>=1.45)
    budget=6 if high_pressure else 5  # one extra opening slot for the logistics fleet
    out=[]
    for r in req:
        r.quantity=min(r.quantity,budget)
        if r.quantity<=0: break
        out.append(r); budget-=r.quantity
        if budget<=0: break
    return out
