
from __future__ import annotations
from dataclasses import dataclass
import math

from .fuel_planner import best_range_ly,has_ife,ENGINE_DATA


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
            'item':'ship_design',
            'design_slot':self.design_slot,
            'design_name':self.design_name,
            'quantity':self.quantity,
            'role':self.role,
        }


def _designs(state):
    return {
        int(d['design_number']):d
        for d in state.native.get('design_profiles',[])
        if not d.get('is_starbase',False)
    }


def _current(state):
    out={}
    for f in state.fleets:
        if f.owner!=state.player_id:
            continue
        for i,n in enumerate((f.native or {}).get('ship_count',[])):
            if n:
                out[i]=out.get(i,0)+int(n)
    return out


def _queued(state):
    out={}
    for qs in state.native.get('production_by_planet',{}).values():
        for q in qs:
            if int(q.get('item_type',0))==4:
                slot=int(q.get('item_id',0))
                out[slot]=out.get(slot,0)+int(q.get('count',0))
    return out


def _role_count(ds,counts,role):
    return sum(
        n for s,n in counts.items()
        if ds.get(s,{}).get('role')==role
    )


def _design_free_cruise(d)->int:
    eid=d.get('engine_id')
    if eid not in ENGINE_DATA:
        return 0
    table=ENGINE_DATA[eid][2]
    out=0
    for w in range(1,min(9,len(table)-1)+1):
        if int(table[w])==0:
            out=w
    return out


def _pick(state,role):
    ds=[d for d in _designs(state).values() if d.get('role')==role]
    if not ds:
        return None

    if role=='scout':
        ife=has_ife(state.race)
        # A free-cruise scout is strategically superior to a slightly longer
        # high-warp design because it can remain on a one-way frontier forever.
        return max(
            ds,
            key=lambda d:(
                _design_free_cruise(d),
                best_range_ly(d,7,ife),
                best_range_ly(d,6,ife),
                d.get('fuel_capacity',0),
                -d.get('dry_mass',999999),
            ),
        )

    if role=='colony':
        ife=has_ife(state.race)
        return max(
            ds,
            key=lambda d:(
                best_range_ly(d,7,ife),
                d.get('fuel_capacity',0),
                -d.get('dry_mass',999999),
            ),
        )

    if role=='miner':
        return max(
            ds,
            key=lambda d:(
                d.get('fuel_capacity',0)/max(1,d.get('dry_mass',1)),
                -d.get('dry_mass',0),
            ),
        )
    return ds[0]


def _desired_scout_force(state,plan,current_scout_assets:int)->tuple[int,str]:
    watchdog=(state.native or {}).get('strategic_watchdog') or {}
    milestone=watchdog.get('milestone') or {}
    unknown=sum(1 for p in state.planets if not p.observed)
    if unknown<=0:
        return 0,"galaxy fully observed"

    turn=max(0,int(state.year)-2400)
    deadline=int(milestone.get('deadline_turn',turn+10))
    explored=int(watchdog.get(
        'explored_count',
        sum(1 for p in state.planets if p.observed),
    ))
    optimal=int(milestone.get(
        'explored_optimal',
        min(len(state.planets),explored+20),
    ))
    turns_left=max(1,deadline-turn)
    gap=max(0,optimal-explored)
    required_rate=gap/turns_left

    recent=float(watchdog.get('discoveries_last_5_turns',0))/5.0
    live=max(1,int(current_scout_assets))
    observed_per_scout=recent/live if recent>0 else .75
    # Do not let one anomalous five-turn window create absurd estimates.
    productivity=min(1.6,max(.55,observed_per_scout))

    throughput_force=math.ceil(required_rate/productivity) if gap>0 else 0
    persona_floor=3 if (plan and plan.objective('scout')>1.15) else 2
    desired=max(persona_floor,throughput_force)

    pressure=float(watchdog.get('exploration_pressure',1.0))
    if pressure>=1.75:
        desired+=2
    elif pressure>=1.45:
        desired+=1

    desired=min(12,max(2,int(desired)))
    desired=min(desired,unknown)

    reason=(
        f"exploration milestone needs {gap} more known worlds by T{deadline} "
        f"(~{required_rate:.2f}/turn); measured scout productivity "
        f"~{productivity:.2f} discoveries/scout-turn; target scout force={desired}"
    )
    return desired,reason


def _desired_colony_force(state,viable,plan)->tuple[int,str]:
    """
    Keep a useful colony pipeline without manufacturing a parking lot of empty
    colony hulls.

    Demand is bounded by:
      - actual known viable claims;
      - exportable 25k population packets;
      - a small pipeline allowance;
      - opening/midgame concurrency cap.
    """
    if not viable:
        return 0,"no known viable unowned claims"

    turn=max(0,int(state.year)-2400)
    owned=[p for p in state.planets if p.owner==state.player_id]

    # Preserve 50k on each source before counting 25k export packets.
    export_packets=sum(
        max(0,(int(p.population or 0)-50000)//25000)
        for p in owned
    )
    loaded=sum(
        1 for f in state.fleets
        if f.owner==state.player_id
        and f.role=='colony'
        and int(f.cargo_population or 0)>=25000
    )

    watchdog=(state.native or {}).get('strategic_watchdog') or {}
    pressure=float(watchdog.get('colonization_pressure',1.0))

    if turn<=10:
        concurrency_cap=3
    elif turn<=25:
        concurrency_cap=4
    else:
        concurrency_cap=6
    if pressure>=1.75:
        concurrency_cap+=1

    # One empty hull may wait in the pipeline; beyond that, require population
    # that can actually launch colonies.
    supported=max(1,loaded+min(export_packets,concurrency_cap)+1)
    desired=min(len(viable),concurrency_cap,supported)

    reason=(
        f"{len(viable)} viable unowned claims; {export_packets} exportable "
        f"25k population packets; {loaded} colony ships already loaded; "
        f"target concurrent colony force={desired}"
    )
    return int(desired),reason


def plan_objective_ship_builds(state,plan=None):
    ds=_designs(state)
    if not ds:
        return []

    cur=_current(state)
    que=_queued(state)
    counts={
        k:cur.get(k,0)+que.get(k,0)
        for k in set(cur)|set(que)
    }
    req=[]

    viable=[
        p for p in state.planets
        if p.observed
        and p.owner is None
        and p.habitability is not None
        and p.habitability>=25
    ]

    colony_assets=_role_count(ds,counts,'colony')
    desired_colonies,colony_reason=_desired_colony_force(state,viable,plan)
    gap=max(0,desired_colonies-colony_assets)
    if gap and (d:=_pick(state,'colony')):
        req.append(BuildRequest(
            'colony',
            int(d['design_number']),
            d['name'],
            min(2,gap),
            130,
            f"{colony_reason}; available/queued={colony_assets}.",
        ))

    unknown=sum(1 for p in state.planets if not p.observed)
    scout_assets=_role_count(ds,counts,'scout')
    desired_scouts,scout_reason=_desired_scout_force(
        state,plan,scout_assets
    )
    scout_gap=max(0,desired_scouts-scout_assets)
    if scout_gap and unknown and (d:=_pick(state,'scout')):
        pressure=float(
            ((state.native or {}).get('strategic_watchdog') or {}).get(
                'exploration_pressure',1.0
            )
        )
        priority=105
        if pressure>=1.75:
            priority=145
        elif pressure>=1.45:
            priority=135
        elif pressure>=1.20:
            priority=118
        req.append(BuildRequest(
            'scout',
            int(d['design_number']),
            d['name'],
            min(3,scout_gap),
            priority,
            (
                f"{unknown} worlds remain unknown; {scout_reason}; "
                f"available/queued={scout_assets}. Existing scout choice favors "
                f"free cruise then efficient Warp-7 range."
            ),
        ))

    mt=[]
    for p in state.planets:
        c=(p.native or {}).get('mineral_concentrations')
        if (
            p.owner is None
            and p.observed
            and c and len(c)>=3
            and all(v is not None for v in c[:3])
            and sum(max(0,int(v)) for v in c[:3])>=150
        ):
            mt.append(p)

    desired_miners=min(3,max(1,math.ceil(len(mt)/2))) if mt else 0
    miner_assets=_role_count(ds,counts,'miner')
    miner_gap=max(0,desired_miners-miner_assets)
    if miner_gap and (d:=_pick(state,'miner')):
        req.append(BuildRequest(
            'miner',
            int(d['design_number']),
            d['name'],
            1,
            85,
            (
                f"{len(mt)} strong observed remote-mining targets; "
                f"desired miners={desired_miners}, available/queued={miner_assets}."
            ),
        ))

    req.sort(key=lambda r:r.priority,reverse=True)

    watchdog=(state.native or {}).get('strategic_watchdog') or {}
    high_pressure=(
        float(watchdog.get('exploration_pressure',1.0))>=1.45
        or float(watchdog.get('colonization_pressure',1.0))>=1.45
    )
    budget=5 if high_pressure else 4
    out=[]
    for r in req:
        r.quantity=min(r.quantity,budget)
        if r.quantity<=0:
            break
        out.append(r)
        budget-=r.quantity
        if budget<=0:
            break
    return out
