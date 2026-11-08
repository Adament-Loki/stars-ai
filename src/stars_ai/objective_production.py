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
from .logistics_capacity import (
    BULK_LARGE_FREIGHTER_TRIGGER_KT,
    POPULATION_DELIVERY_HORIZON_COLONISTS,
    POPULATION_PULSE_COLONISTS,
    POPULATION_PULSE_KT,
    evaluate_logistics_capacity,
    population_transport_throughput,
)
from .population_invasion import population_capture_targets
from .scout_policy import enemy_contact_summary, custom_scout_missions
from .territorial_defense import assess_territorial_defense, hostile_colonies_inside_claimed_area
from .planet_economy import decode_race_economy, working_mineral_reserve
from .capability_execution import capability_status, design_capability_id, profile_is_blocked
from .turn_state import mapping as turn_mapping


# Stars! production queues are serial at a given yard.  Counting every ship
# buried in one as a usable asset made the expansion planner believe that a
# twenty-ship Mayflower backlog was a twenty-ship colony force.  Only the next
# small horizon can reasonably affect the current strategic decision.
IMMEDIATE_SHIP_QUEUE_HORIZON = 2
LOW_TECH_ESCORT_WEAPONS = 6
LOW_TECH_ESCORT_CAP = 2
# A new weapon/hull generation needs a tiny real-world proving force, but it
# must not authorize a second full fleet while the prior generation remains
# available.  The rest of the force turns over through losses, retirement, and
# ordinary force growth.
MODERNIZATION_SEED_HULLS = 2
# Default maximum simultaneous colony pipeline. A game/configuration may set
# ``native.colony_pipeline_cap`` to a tighter or broader program without
# changing the source-backed claim invariant below.
DEFAULT_COLONY_PIPELINE_CAP = 24


def _watchdog(state):
    return turn_mapping(state, "strategic_watchdog")


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
            # Queue assembly uses this to protect only a short urgent horizon
            # ahead of installations; the native encoder ignores it.
            'strategic_priority':int(self.priority),
            'strategic_reason':self.reason,
        }


def _designs(state):
    return {
        int(d['design_number']):d
        for d in state.native.get('design_profiles',[])
        if not d.get('is_starbase',False)
        and not profile_is_blocked(state,d,is_starbase=False)
    }


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


def _immediate_queued(state, horizon:int=IMMEDIATE_SHIP_QUEUE_HORIZON):
    """Count only ships that are near the front of each serial yard queue."""
    out={}
    for qs in state.native.get('production_by_planet',{}).values():
        remaining=max(0,int(horizon))
        for q in qs:
            if remaining<=0:
                break
            if int(q.get('item_type',0))!=4 or not 0<=int(q.get('item_id',0))<16:
                continue
            slot=int(q.get('item_id',0))
            quantity=min(remaining,max(0,int(q.get('count',0) or 0)))
            if quantity:
                out[slot]=out.get(slot,0)+quantity
                remaining-=quantity
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
    """Return the actual role-force gap, with combat-only modernization.

    When an upgraded hull arrives, retain a small proving force of the new
    design but do not treat the whole target force as vacant.  Old ships still
    defend, escort, and absorb attrition; replacing every one immediately
    creates an uneconomical duplicate fleet of the same mission.
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
    # Survey, colony, and logistics hulls are capability counts, not a
    # replacement fleet.  Applying a new-generation proving throttle to them
    # can turn ``desired=24, useful=2`` into zero production merely because a
    # newer scout profile exists.  Combat alone has a valid modernization
    # concern: older armed hulls continue to defend while a new architecture
    # proves itself.
    if role == "combat" and selected_generation>0 and older_assets>0:
        # The seed permits a newly researched weapons/hull architecture to be
        # field-tested even when the old force already meets the territorial
        # target.  Once it exists, all generations share the same hard force
        # ceiling.  This prevents a W10/W12 upgrade from duplicating an entire
        # fleet of early escorts.
        ceiling=int(desired) + (
            MODERNIZATION_SEED_HULLS
            if selected_assets < MODERNIZATION_SEED_HULLS else 0
        )
        return max(0,ceiling-int(role_assets)),(
            f"modernization throttle: newest {role} generation T{selected_generation} has "
            f"{selected_assets} live/imminent; {older_assets} older hull(s) remain useful. "
            f"Total force ceiling={ceiling} (base target={desired}, proving seed="
            f"{MODERNIZATION_SEED_HULLS} only until fielded)"
        )
    gap=max(0,int(desired)-int(role_assets))
    return gap,(
        f"role-force accounting: desired total={int(desired)}, useful live/imminent="
        f"{int(role_assets)}, build gap={gap}; generation does not suppress non-combat force demand"
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
    """Choose a population carrier appropriate to current source surplus.

    The choice is driven by the largest load a source can currently give to a
    child that still needs it.  Ordinary lanes favour economical compact hulls;
    a larger hull is preferred only when its extra hold can be used now.
    """
    ds=[d for d in _freighter_profiles(state) if int(d.get('cargo_capacity',0) or 0)>=POPULATION_PULSE_KT]
    if not ds:
        return None
    logistics=evaluate_logistics_capacity(state)
    useful_load_kt=max((
        min(
            int(status.exportable_population or 0),
            int(status.downstream_backlog or 0),
        ) // 100
        for status in logistics.exporter_status
    ), default=0)
    if useful_load_kt > 0:
        ife=has_ife(state.race)
        return max(ds,key=lambda d:(
            min(int(d.get('cargo_capacity',0) or 0), useful_load_kt),
            -max(0, int(d.get('cargo_capacity',0) or 0) - useful_load_kt),
            _design_generation(d),
            best_range_ly(d,7,ife),
            float(d.get('fuel_capacity',0) or 0)/max(1,int(d.get('dry_mass',1) or 1)),
            -int(d.get('dry_mass',999999) or 999999),
        ))
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


def _scout_scanner_score(design: dict) -> int:
    """Normalize scanner evidence from decoded profiles and component lists."""
    direct=max(
        int(design.get("scanner_range",0) or 0),
        int(design.get("normal_scan_range",0) or 0),
    )
    components=design.get("components",[]) or design.get("slots",[]) or []
    fitted=sum(
        max(0,int(component.get("count",0) or 0)) * (1+int(component.get("item_id",0) or 0))
        for component in components
        if isinstance(component,dict) and int(component.get("category",0) or 0)==256
    )
    return max(direct,fitted)


def _scout_weapon_score(design: dict) -> int:
    direct=max(int(design.get("weapon_count",0) or 0), int(design.get("weapons",0) or 0))
    components=design.get("components",[]) or design.get("slots",[]) or []
    fitted=sum(
        max(0,int(component.get("count",0) or 0)) * (1+int(component.get("item_id",0) or 0))
        for component in components
        if isinstance(component,dict) and int(component.get("category",0) or 0) in (16,32,64)
    )
    return max(direct,fitted)


def _scout_resource_cost(design: dict) -> int:
    raw=design.get("resource_cost",design.get("cost",0))
    if isinstance(raw,dict):
        return sum(max(0,int(value or 0)) for value in raw.values())
    try:
        return max(0,int(raw or 0))
    except (TypeError,ValueError):
        return 0


def _pick(state,role,*,mission: str="survey"):
    ds=[d for d in _designs(state).values() if d.get('role')==role]
    if not ds:
        return None
    if role=='scout':
        ife=has_ife(state.race)
        if str(mission).casefold() in {"armed_recon","contested_border","border_recon"}:
            # Combat fittings matter only when the caller explicitly assigns
            # an armed reconnaissance mission.  A viable weapon is preferred,
            # then the usual safe range/scanner/cost trade-offs apply.
            return max(ds,key=lambda d:(
                int(_scout_weapon_score(d)>0),_scout_weapon_score(d),
                best_range_ly(d,7,ife),_scout_scanner_score(d),
                _design_free_cruise(d),- _scout_resource_cost(d),
                -d.get('dry_mass',999999),_design_generation(d),
            ))
        # Normal survey is a coverage job.  Never let a newer armed but
        # short-range probe displace an older scanner/range design merely
        # because weapon components happened to raise its design generation.
        return max(ds,key=lambda d:(
            best_range_ly(d,7,ife),_scout_scanner_score(d),
            _design_free_cruise(d),best_range_ly(d,6,ife),
            float(d.get('fuel_capacity',0) or 0)/max(1,int(d.get('dry_mass',1) or 1)),
            -_scout_resource_cost(d),-d.get('dry_mass',999999),_design_generation(d),
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
    watchdog=_watchdog(state); milestone=watchdog.get('milestone') or {}
    unknown=sum(1 for p in state.planets if not p.observed)
    contact=enemy_contact_summary(state)
    custom_missions=custom_scout_missions(state)
    scout_weight=plan.objective('scout') if plan else 1.0
    phase_name=str(getattr(plan,'phase','') or '') if plan else ''
    late_posture=phase_name in {
        'fortification','opportunistic_war','tech_acceleration','industrial_buildout',
    }
    midgame=phase_name=='midgame'
    if contact["enemy_contact"]:
        # Contact changes where scouts fly; it must not end expansion.  We
        # retain a real border screen while continuing to map uncontested
        # space.  This is deliberately a much larger force than the old
        # contact-limited two-scout policy.
        required=min(8,len(custom_missions))
        coverage=math.ceil(unknown/6) if unknown>0 else 0
        # A mature defensive/attack posture retains border intelligence but
        # stops treating every foreign sighting as an expansion-race demand.
        phase_floor=2 if late_posture else 4 if midgame else 8
        desired=min(24,max(phase_floor if unknown>0 else 0,required,coverage))
        purposes=", ".join(str(m["id"]) for m in custom_missions[:6]) or "open-frontier coverage"
        return desired,(
            "foreign contact is established; maintain an expansion-race scout screen for "
            f"uncontested exploration and border intelligence [{purposes}]. "
            f"Target force={desired}; exploration continues with persistent contact-aware coverage"
            + (" while the mature posture reserves production for defense/attack." if late_posture else ".")
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
    if late_posture:
        persona_floor=2
    elif midgame:
        persona_floor=4
    elif scout_weight>1.15:
        persona_floor=9
    else:
        persona_floor=8
    desired=max(persona_floor,throughput_force); pressure=float(watchdog.get('exploration_pressure',1.0))
    if pressure>=1.75: desired+=6
    elif pressure>=1.45: desired+=4
    elif pressure>=1.20: desired+=2
    # The milestone is a minimum completion obligation.  Abundant unexplored
    # space is its own opportunity, so density can lift the program beyond the
    # deadline calculation instead of treating that calculation as a ceiling.
    opportunity_force=math.ceil(unknown/6)
    minimum=2 if late_posture else 4 if midgame else 8
    desired=min(24,max(minimum,int(desired),opportunity_force)); desired=min(desired,unknown)
    return desired,(f"exploration milestone needs {gap} more known worlds by T{deadline} (~{required_rate:.2f}/turn); "
                    f"measured scout productivity ~{productivity:.2f} discoveries/scout-turn; target scout force={desired}")


def _colony_pipeline_capacity(state, viable) -> dict:
    """Calculate the empire-wide colony-hull target from real claim capacity.

    A colony hull is a delivery mechanism for a known viable claim, not a
    standing speculative fleet.  Existing commitments occupy their own claim
    slots; remaining slots are bounded only by phase-eligible claims, population
    packets that can actually be exported, and the configured pipeline cap.
    """
    turn=max(0,int(state.year)-2400); owned=[p for p in state.planets if p.owner==state.player_id]
    source_reserve=colony_source_reserve_for_turn(turn)
    export_packets=sum(max(0,(int(p.population or 0)-source_reserve)//COLONY_LOAD_COLONISTS) for p in owned)
    loaded=sum(1 for f in state.fleets if f.owner==state.player_id and f.role=='colony' and int(f.cargo_population or 0)>=COLONY_LOAD_COLONISTS)
    watchdog=_watchdog(state); pressure=float(watchdog.get('colonization_pressure',1.0))
    phase_cap=15 if turn<=5 else DEFAULT_COLONY_PIPELINE_CAP if turn<=15 else 32
    concurrency_cap=phase_cap
    if pressure>=1.75: concurrency_cap+=10
    elif pressure>=1.45: concurrency_cap+=6
    raw_cap=(state.native or {}).get("colony_pipeline_cap")
    try:
        configured_cap=max(1,int(raw_cap)) if raw_cap is not None else int(concurrency_cap)
    except (TypeError,ValueError):
        configured_cap=int(concurrency_cap)
    viable_ids={int(planet.id) for planet in viable}
    active_claim_ids={
        int(f.destination_planet_id)
        for f in state.fleets
        if f.owner==state.player_id
        and f.role=='colony'
        and f.destination_planet_id is not None
    }
    committed_claim_ids=active_claim_ids & viable_ids
    committed_claims=len(committed_claim_ids)
    uncommitted_claim_ids=viable_ids-active_claim_ids
    sustainable_uncommitted=max(0,export_packets-loaded)
    sustainable_population_packets=committed_claims+sustainable_uncommitted
    # This is the hard pipeline invariant: when there are uncommitted valid
    # claims, target every one that has a sustainable packet, up to the
    # configured concurrency ceiling. Existing committed claims remain counted
    # even if population information is sparse in the current M-file.
    desired=min(
        len(viable_ids),
        configured_cap,
        sustainable_population_packets,
    )
    desired=max(committed_claims,desired)
    return {
        "claim_count":len(viable_ids),
        "phase_eligible_claim_ids":sorted(viable_ids),
        "committed_claim_ids":sorted(committed_claim_ids),
        "committed_colony_fleets":committed_claims,
        "uncommitted_claim_ids":sorted(uncommitted_claim_ids),
        "uncommitted_claim_count":len(uncommitted_claim_ids),
        "source_reserve":source_reserve,
        "exportable_population_packets":export_packets,
        "loaded_colony_packets":loaded,
        "sustainable_uncommitted_packets":sustainable_uncommitted,
        "sustainable_population_packets":sustainable_population_packets,
        "configured_cap":configured_cap,
        "desired_colony_pipeline":int(desired),
    }


def _desired_colony_force(state,viable,plan)->tuple[int,str]:
    if not viable:
        policy=colonization_policy(state,plan)
        floor=("resource-driven universal habitability" if policy.normal_habitability_floor is None else f"race-adjusted habitability floor {policy.normal_habitability_floor}%")
        return 0,f"no known {policy.stage} claims meeting {floor}"
    pipeline=_colony_pipeline_capacity(state,viable)
    turn=max(0,int(state.year)-2400)
    watchdog=_watchdog(state)
    milestone=watchdog.get('milestone') or {}; deadline=int(milestone.get('deadline_turn',turn+10) or turn+10)
    new_colonies=int(watchdog.get('new_colonies',0) or 0); optimal=int(milestone.get('new_colonies_optimal',new_colonies+4) or (new_colonies+4))
    turns_left=max(1,deadline-turn); settlement_gap=max(0,optimal-new_colonies); required_rate=settlement_gap/turns_left
    desired=int(pipeline["desired_colony_pipeline"])
    reason=(f"{pipeline['claim_count']} phase-eligible unowned claims under {colonization_policy(state,plan).stage}; {pipeline['exportable_population_packets']} exportable colony population packets "
            f"({COLONY_LOAD_KT} kT / {COLONY_LOAD_COLONISTS:,} colonists each); source reserve={pipeline['source_reserve']:,}; "
            f"milestone needs {settlement_gap} more colonies by T{deadline} (~{required_rate:.2f}/turn); {pipeline['loaded_colony_packets']} colony ships already loaded; "
            f"pipeline invariant=min(claims={pipeline['claim_count']}, sustainable packets={pipeline['sustainable_population_packets']} "
            f"({pipeline['committed_colony_fleets']} committed + {pipeline['sustainable_uncommitted_packets']} source-backed uncommitted), cap={pipeline['configured_cap']}); "
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


def _idle_empty_freighters(state):
    """Return empty, destinationless freight hulls parked at an owned world.

    A transport already waiting at *any* owned planet is usable capacity.  Do
    not build another hull simply because it is not at the homeworld or does
    not match this turn's first selected lane.
    """
    owned = [p for p in state.planets if p.owner == state.player_id]
    idle_ids: list[int] = []
    idle_hulls = 0
    for fleet in state.fleets:
        if fleet.owner != state.player_id or fleet.role != "freighter":
            continue
        if fleet.destination_planet_id is not None:
            continue
        cargo = (fleet.native or {}).get("cargo", {}) or {}
        if any(int(cargo.get(key, 0) or 0) for key in ("ironium", "boranium", "germanium", "population")):
            continue
        capacity = int(
            getattr(fleet, "cargo_capacity", 0)
            or (fleet.native or {}).get("cargo_capacity", 0)
            or 0
        )
        if capacity <= 0:
            continue
        parked = any(
            int((fleet.native or {}).get("position_object_id", -1)) == int(planet.id)
            or (
                abs(float(fleet.position.x) - float(planet.position.x)) <= 0.5
                and abs(float(fleet.position.y) - float(planet.position.y)) <= 0.5
            )
            for planet in owned
        )
        if not parked:
            continue
        idle_ids.append(int(fleet.id))
        hulls = sum(max(0, int(count or 0)) for count in ((fleet.native or {}).get("ship_count", []) or []))
        idle_hulls += max(1, hulls)
    return tuple(sorted(idle_ids)), int(idle_hulls)


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



REMOTE_MINING_MIN_TURN = 20
REMOTE_MINING_LATE_TURN = 30
REMOTE_MINING_REAL_PRESSURE_RATIO = 0.25
REMOTE_MINING_SEVERE_PRESSURE_RATIO = 0.50


def _is_alternate_reality(state) -> bool:
    race=getattr(state,"race",None)
    native=getattr(race,"native",{}) or {}
    try:
        prt_id=int(native.get("prt_id",-1))
    except (TypeError,ValueError):
        prt_id=-1
    prt=str(getattr(race,"primary_trait","") or "").casefold()
    return prt_id==8 or "alternate reality" in prt


def _remote_mining_pressure_policy(state,plan,remote_targets) -> dict:
    # Remote miners are a capital-intensive mid/late-game tool. Merely seeing
    # a rich rock is not enough to build them. The empire must be mature and
    # materially below the shared working-mineral reserve used by economics.
    owned=[p for p in state.planets if p.owner==state.player_id]
    turn=max(0,int(state.year)-2400)
    phase=str(getattr(plan,"phase","") or "")
    late_phase=phase in {
        "industrial_buildout","fortification","opportunistic_war","tech_acceleration",
    }
    economy=decode_race_economy(state.race)

    surface={"ironium":0,"boranium":0,"germanium":0}
    reserve={"ironium":0,"boranium":0,"germanium":0}
    stressed_worlds=0
    per_world=[]
    for planet in owned:
        need=working_mineral_reserve(planet,economy,())
        world_surface={
            "ironium":max(0,int(getattr(planet,"ironium",0) or 0)),
            "boranium":max(0,int(getattr(planet,"boranium",0) or 0)),
            "germanium":max(0,int(getattr(planet,"germanium",0) or 0)),
        }
        short={k:max(0,int(need.get(k,0) or 0)-world_surface[k]) for k in surface}
        if sum(short.values())>0:
            stressed_worlds+=1
        for k in surface:
            surface[k]+=world_surface[k]
            reserve[k]+=max(0,int(need.get(k,0) or 0))
        per_world.append({
            "planet_id":int(planet.id),
            "surface":world_surface,
            "working_reserve":{k:int(need.get(k,0) or 0) for k in surface},
            "deficit":short,
        })

    deficits={k:max(0,reserve[k]-surface[k]) for k in surface}
    reserve_total=max(1,sum(reserve.values()))
    deficit_total=sum(deficits.values())
    pressure_ratio=deficit_total/reserve_total
    critical_deficit=max(deficits.values()) if deficits else 0

    mature=bool(
        turn>=REMOTE_MINING_MIN_TURN
        and (late_phase or len(owned)>=6 or turn>=REMOTE_MINING_LATE_TURN)
    )
    real_pressure=bool(
        mature
        and (
            pressure_ratio>=REMOTE_MINING_REAL_PRESSURE_RATIO
            or (
                stressed_worlds>=max(2,(len(owned)+2)//3)
                and critical_deficit>=120
            )
        )
    )
    severe_pressure=bool(
        real_pressure and pressure_ratio>=REMOTE_MINING_SEVERE_PRESSURE_RATIO
    )
    is_ar=_is_alternate_reality(state)

    if not real_pressure:
        desired=0
    elif is_ar and turn>=REMOTE_MINING_LATE_TURN:
        # AR late-game doctrine: once real pressure exists, build a fleet rather
        # than one or two token miners for homeworld remote-mining leverage.
        desired=min(16,max(6,6+int(round(pressure_ratio*10))))
    else:
        target_count=len(remote_targets)
        base=min(5,max(1,(target_count+1)//2)) if target_count else 0
        desired=min(6,base+(2 if severe_pressure else 0))

    return {
        "turn":turn,
        "phase":phase,
        "alternate_reality":is_ar,
        "mature_empire":mature,
        "real_mineral_pressure":real_pressure,
        "severe_mineral_pressure":severe_pressure,
        "pressure_ratio":round(float(pressure_ratio),3),
        "surface_stock":surface,
        "working_reserve":reserve,
        "reserve_deficit":deficits,
        "stressed_worlds":stressed_worlds,
        "owned_worlds":len(owned),
        "observed_remote_targets":len(remote_targets),
        "desired_miners":int(desired),
        "world_diagnostics":per_world,
        "policy":(
            "Do not build remote miners merely because rich targets are visible. "
            "Require a mature empire plus real working-reserve mineral pressure. "
            "Late-game AR may scale to a large homeworld mining fleet after that gate opens."
        ),
    }



def plan_objective_ship_builds(state,plan=None):
    ds=_designs(state)
    if not ds: return []
    cur=_current(state)
    deep_queued=_queued(state)
    imminent_queued=_immediate_queued(state)
    counts={k:cur.get(k,0)+imminent_queued.get(k,0) for k in set(cur)|set(imminent_queued)}
    req=[]
    viable=[p for p in state.planets if colony_planet_is_eligible(state,p,plan)]

    colony_assets=_role_count(ds,counts,'colony')
    active_claims=[int(f.destination_planet_id) for f in state.fleets if f.owner==state.player_id and f.role=='colony' and f.destination_planet_id is not None]
    duplicate_commitments=max(0,len(active_claims)-len(set(active_claims))); effective_colony_assets=max(0,colony_assets-duplicate_commitments)
    desired_colonies,colony_reason=_desired_colony_force(state,viable,plan)
    colony_launch_shortfall=max(0,desired_colonies-effective_colony_assets)
    pipeline=(
        _colony_pipeline_capacity(state,viable)
        if viable else {
            "claim_count":0,"phase_eligible_claim_ids":[],"committed_claim_ids":[],
            "committed_colony_fleets":0,"uncommitted_claim_ids":[],
            "uncommitted_claim_count":0,"exportable_population_packets":0,
            "loaded_colony_packets":0,"sustainable_uncommitted_packets":0,
            "sustainable_population_packets":0,
            "configured_cap":0,"desired_colony_pipeline":0,
        }
    )
    state.native["colony_pipeline"]={
        **pipeline,
        "live_imminent_colony_hulls":int(colony_assets),
        "effective_distinct_claim_pipeline":int(effective_colony_assets),
        "duplicate_active_commitments":int(duplicate_commitments),
        "required_new_hulls":int(colony_launch_shortfall),
        "short_queue_horizon":IMMEDIATE_SHIP_QUEUE_HORIZON,
        "build_authorized":bool(viable and colony_launch_shortfall),
        "policy":(
            "Reserve a short executable colony-hull horizon whenever a phase-eligible "
            "uncommitted claim has sustainable population capacity and no live/imminent hull."
        ),
    }
    if d:=_pick(state,'colony'):
        gap,generation_reason=_selected_role_gap(
            ds,counts,'colony',d,desired_colonies,effective_colony_assets,
        )
        if gap:
            pressure=float(_watchdog(state).get('colonization_pressure',1.0))
            priority=200 if pressure>=1.45 else 182
            req.append(BuildRequest('colony',int(d['design_number']),d['name'],min(IMMEDIATE_SHIP_QUEUE_HORIZON,gap),priority,
                f"{colony_reason}; live/imminent={colony_assets}; effective distinct-claim pipeline={effective_colony_assets}; duplicate active commitments={duplicate_commitments}; deep queue total={_role_count(ds,deep_queued,'colony')}; {generation_reason}."))

    unknown=sum(1 for p in state.planets if not p.observed); scout_assets=_role_count(ds,counts,'scout')
    scout_contact=enemy_contact_summary(state)
    scout_custom_missions=custom_scout_missions(state)
    desired_scouts,scout_reason=_desired_scout_force(state,plan,scout_assets)
    if viable and colony_launch_shortfall:
        # Colony launches must not consume the whole early production window,
        # but a two-scout ceiling repeatedly left both empires blind.  Let the
        # size of the unexplored frontier set the protected survey screen; a
        # large map can therefore sustain its full required scout force while
        # a small one does not create a speculative probe backlog.
        frontier_screen=math.ceil(math.sqrt(max(0,unknown)))
        scout_cap=max(_role_count(ds,cur,'scout'),min(desired_scouts,frontier_screen))
        if desired_scouts>scout_cap:
            desired_scouts=scout_cap
            scout_reason+=(
                f" Colony-compatible frontier screen={scout_cap}: {colony_launch_shortfall} "
                "colony hull(s) are still missing from the live/imminent pipeline."
            )
    scout_build_authorized=bool(unknown)
    if scout_build_authorized and (d:=_pick(state,'scout')):
        scout_gap,generation_reason=_selected_role_gap(ds,counts,'scout',d,desired_scouts,scout_assets)
        if not scout_gap:
            generation_reason=""
    else:
        scout_gap=0
        generation_reason=""
    state.native["scout_build_policy"]={
        **scout_contact,
        "classic_exploration_enabled":True,
        "contact_limited_scout_building":bool(scout_contact["enemy_contact"]),
        "custom_missions":scout_custom_missions,
        "desired_scout_force":int(desired_scouts),
        "live_imminent_scout_force":int(scout_assets),
        "actual_scout_build_gap":int(scout_gap),
    }
    if scout_gap and scout_build_authorized and (d:=_pick(state,'scout')):
        pressure=float(_watchdog(state).get('exploration_pressure',1.0))
        priority=196 if pressure>=1.75 else 186 if pressure>=1.45 else 172 if pressure>=1.20 else 160
        if scout_contact["enemy_contact"] and scout_custom_missions:
            priority=max(priority,max(int(m.get("priority",110) or 110) for m in scout_custom_missions))
        req.append(BuildRequest('scout',int(d['design_number']),d['name'],min(6,scout_gap),priority,
            f"{unknown} worlds remain unknown; {scout_reason}; live/imminent={scout_assets}. "
            f"Survey-scout selection favors usable Warp-7 range, scanner capability, efficient cruise, and low cost/mass; {generation_reason}."))

    # Population freight and industrial bulk freight are separate jobs, but a
    # a source with a large usable population surplus can justify a
    # high-capacity carrier instead of several smaller departures.
    population_assets,_bulk_assets,total_freighter_assets=_freighter_asset_counts(ds,counts)
    desired_population,desired_bulk,freighter_reason,logistics=_desired_freighter_forces(
        state,len(viable),
    )
    idle_freighter_ids, idle_freighter_hulls = _idle_empty_freighters(state)
    throughput=population_transport_throughput(state,logistics)
    population_backlog=int(throughput["backlog_colonists"])
    # A population hull is justified by a real unserved lane, not an arbitrary
    # number of 20k packets.  Bulk freight retains its separate industrial
    # material threshold.
    material_population_shortfall = bool(
        population_backlog >= 20_000
        and int(throughput["exportable_population"]) >= 20_000
        and int(throughput["transport_shortfall"]) > 0
    )
    significant_freight_backlog = bool(
        (
            material_population_shortfall
        )
        or int(logistics.bulk_transferable_kt) >= BULK_LARGE_FREIGHTER_TRIGGER_KT
    )
    freighter_build_authorized = bool(
        significant_freight_backlog
        and (
            not idle_freighter_ids
            # A single idle hull is useful, but it must not conceal a large
            # capacity deficit.  Assign it this turn and queue compact relief.
            or int(throughput["transport_shortfall"]) > 0
        )
    )
    state.native["freighter_build_policy"] = {
        "idle_empty_freighter_ids": list(idle_freighter_ids),
        "idle_empty_freighter_hulls": idle_freighter_hulls,
        "population_backlog_colonists": population_backlog,
        "population_transport_shortfall_kt": int(throughput["transport_shortfall"]),
        "material_population_shortfall": material_population_shortfall,
        "bulk_transferable_kt": int(logistics.bulk_transferable_kt),
        "significant_backlog": significant_freight_backlog,
        "build_authorized": freighter_build_authorized,
        "reason": (
            "idle freight will be reserved for population and compact relief is queued for the remaining shortfall"
            if idle_freighter_ids and int(throughput["transport_shortfall"]) > 0
            else "existing empty transport capacity is parked at an owned planet"
            if idle_freighter_ids
            else "no idle empty transport; backlog must justify a new hull"
        ),
    }
    state.native["population_transport_throughput"] = throughput
    state.native["population_export_capacity"] = {
        "exportable_population":int(logistics.exportable_population),
        "protected_population_floor":int(logistics.protected_population_floor),
        "downstream_population_backlog":int(logistics.downstream_population_backlog),
        "available_freighter_capacity_kt":int(logistics.available_freighter_capacity_kt),
        "active_population_transport_capacity_kt":int(logistics.active_population_transport_capacity_kt),
        "desired_population_transport_capacity_kt":int(logistics.desired_population_transport_capacity_kt),
        "desired_population_transports":int(logistics.desired_population_freighters),
        "live_population_transport_assets":int(population_assets),
        "build_gap":max(0,int(logistics.desired_population_freighters)-int(population_assets)),
        "policy":"stage idle empty freighters at exporters first; build compact population capacity only when the backlog remains above active and queued throughput.",
    }

    population_gap=max(0,desired_population-population_assets)
    if population_gap and freighter_build_authorized and (d:=_pick_freighter_for_population(state)):
        req.append(BuildRequest(
            'freighter',int(d['design_number']),d['name'],min(4,population_gap),174,
            f"{freighter_reason}; population transports available/queued={population_assets}; "
            f"selected {d['name']} cargo={int(d.get('cargo_capacity',0) or 0)} kT, "
            "selected from current protected source surplus and strategic colony backlog."
        ))

    # One shared territorial exposure snapshot controls defensive hull, mine,
    # base, and counter-capture demand.  Local intercepts still happen in the
    # military planner; this is the empire-scale reserve contract.
    territorial = assess_territorial_defense(state, plan)
    exposure = territorial.exposure
    state.native["territorial_defense"] = territorial.to_dict()
    state.native["empire_exposure"] = exposure.to_dict()

    # A visible small foreign colony is a legitimate expansion target for one
    # large transport.  This does not create a fighter program: population
    # capture is gated by an empty 800+ kT hull and the scheduler's breeder,
    # range, population-estimate, and Alternate Reality safeguards.
    capture_targets=population_capture_targets(state)
    capture_capacity=max((int(d.get('cargo_capacity',0) or 0) for d in ds.values() if d.get('role')=='freighter'),default=0)
    needed_capture_capacity=min((target.required_kt for target in capture_targets),default=0)
    live_capture_assets=sum(
        1
        for f in state.fleets
        if f.owner==state.player_id
        and f.role=='freighter'
        and int(getattr(f,'cargo_capacity',0) or (getattr(f,'native',{}) or {}).get('cargo_capacity',0) or 0)
        >=needed_capture_capacity
    ) if needed_capture_capacity else 0
    desired_capture_assets=(
        max(1,int(exposure.counter_capture_transport_target))
        if needed_capture_capacity else 0
    )
    capture_gap=max(0,desired_capture_assets-live_capture_assets)
    if (
        needed_capture_capacity
        and capture_gap
        and capture_capacity>=needed_capture_capacity
        and (d:=_pick_freighter_for_bulk(state)) is not None
        and int(d.get('cargo_capacity',0) or 0)>=needed_capture_capacity
    ):
        req.append(BuildRequest(
            'freighter',int(d['design_number']),d['name'],min(2,capture_gap),
            max(178,int(exposure.counter_capture_priority or 0)),
            f"{len(capture_targets)} small observed foreign colony target(s) qualify for guarded population capture; "
            f"live capture-capable transports={live_capture_assets}, desired={desired_capture_assets}, "
            f"required capacity={needed_capture_capacity}kT. Build {d['name']} for "
            "counter-capture/recapture before routine economic growth."
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
    if (
        bulk_gap
        and freighter_build_authorized
        and bulk_design is not None
        and int(bulk_design.get('cargo_capacity',0) or 0)>=POPULATION_PULSE_KT
    ):
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
    mining_policy=_remote_mining_pressure_policy(state,plan,mt)
    state.native["remote_mining_build_policy"]=mining_policy
    desired_miners=int(mining_policy["desired_miners"])
    miner_assets=_role_count(ds,counts,'miner')
    miner_gap=max(0,desired_miners-miner_assets)
    if miner_gap and (d:=_pick(state,'miner')):
        build_cap=4 if bool(mining_policy["alternate_reality"]) else 2
        req.append(BuildRequest(
            'miner',int(d['design_number']),d['name'],min(build_cap,miner_gap),
            138 if bool(mining_policy["severe_mineral_pressure"]) else 124,
            (
                f"Remote mining authorized by real mineral pressure: pressure ratio="
                f"{float(mining_policy['pressure_ratio']):.1%}, reserve deficit "
                f"I/B/G={mining_policy['reserve_deficit']['ironium']}/"
                f"{mining_policy['reserve_deficit']['boranium']}/"
                f"{mining_policy['reserve_deficit']['germanium']} kT; "
                f"{len(mt)} strong observed unowned target(s); desired miners={desired_miners}, "
                f"available/queued={miner_assets}."
                + (
                    " AR late-game doctrine permits a large homeworld mining fleet once "
                    "the real-pressure gate is open."
                    if bool(mining_policy["alternate_reality"]) else ""
                )
            ),
        ))

    # Minefields are territorial claims, not a default peace-time sink. Build
    # minelayers only after a non-friendly armed or transport fleet enters a
    # perceived zone around an owned world. The military planner uses these
    # same records to deploy the completed hulls and place patrols.
    minelayer_assets = _role_count(ds, counts, 'minelayer')
    minelayer_gap = max(0, int(territorial.desired_minelayers) - minelayer_assets)
    minelayer_design=_pick(state,'minelayer')
    state.native["minelayer_capability_demand"]={
        "desired_minelayers":int(territorial.desired_minelayers),
        "available_or_queued":minelayer_assets,
        "design_available":bool(minelayer_design is not None),
        "execution_status":capability_status(state,design_capability_id("minelayer")),
        "blocked_native":capability_status(state,design_capability_id("minelayer")) == "BLOCKED_NATIVE",
        "policy":"Keep territorial minefield demand open until a host-confirmed minelayer design is available.",
    }
    if minelayer_gap and (d := minelayer_design):
        req.append(BuildRequest(
            'minelayer', int(d['design_number']), d['name'],
            min(4 if exposure.emergency_active else 2, minelayer_gap),
            max(178, 184 if exposure.emergency_active else 0),
            f"{territorial.reason} Minelayers available/queued={minelayer_assets}; "
            f"desired territorial minefield force={territorial.desired_minelayers}; "
            f"exposure target={exposure.minelayer_force_target}."
        ))

    # Build a limited territorial bomber element after the design exists.
    hostile_colonies=hostile_colonies_inside_claimed_area(state,plan)
    bomber_assets=_role_count(ds,counts,'bomber')
    desired_bombers=min(6,max(2,2*len(hostile_colonies))) if hostile_colonies else 0
    bomber_gap=max(0,desired_bombers-bomber_assets)
    bomber_design=_pick(state,'bomber')
    state.native["bomber_capability_demand"]={
        "target_planet_ids":[int(planet.id) for planet in hostile_colonies],
        "desired_bombers":desired_bombers,
        "available_or_queued":bomber_assets,
        "design_available":bool(bomber_design is not None),
        "execution_status":capability_status(state,design_capability_id("bomber")),
        "blocked_native":capability_status(state,design_capability_id("bomber")) == "BLOCKED_NATIVE",
        "policy":"Prepare a small escorted bombing capability only for hostile/opposed colonies inside claimed territory.",
    }
    if bomber_gap and bomber_design is not None:
        req.append(BuildRequest(
            'bomber',int(bomber_design['design_number']),bomber_design['name'],
            min(3,bomber_gap),184,
            f"{len(hostile_colonies)} hostile/opposed colony world(s) are inside the claimed area; "
            f"bombers available/queued={bomber_assets}, desired strike capability={desired_bombers}. "
            "Build a limited escorted bomber element; design capability was seeded separately."
        ))

    # Escort is a mission assigned to the shared combat architecture, not a
    # separate design family.  Once another empire is observed, retain a small
    # real combat force instead of endlessly designing Frigate variants that
    # never reach a production queue.
    military_contact=enemy_contact_summary(state)
    combat_assets=_role_count(ds,counts,'combat')
    owned_count=sum(1 for planet in state.planets if planet.owner==state.player_id)
    # Seeing a distant foreign ship is intelligence, not a reason to queue an
    # eight-hull defense program ahead of an empty colony pipeline. Explicit
    # territorial violations below still get the required response.
    phase_name=str(getattr(plan,'phase','') or '')
    hold_line_worlds=sum(
        1 for role in (getattr(plan,'planet_actions',{}) or {}).values() if role=='hold_line'
    )
    phase_force_floor=(
        min(8,max(4,2*hold_line_worlds))
        if phase_name=='midgame' and hold_line_worlds
        else min(24,max(4,math.ceil(owned_count*0.75)))
        if phase_name=='fortification'
        else min(24,max(6,owned_count))
        if phase_name=='opportunistic_war'
        else 0
    )
    contact_only_deferral=bool(
        military_contact['enemy_contact']
        and colony_launch_shortfall
        and not territorial.needs_response
        and owned_count<8
        and phase_force_floor<=0
    )
    baseline_combat=(
        min(24,max(8,math.ceil(owned_count/2)))
        if military_contact['enemy_contact'] and not contact_only_deferral else 0
    )
    desired_combat=max(
        baseline_combat,
        phase_force_floor,
        min(24, 4 + 2 * int(territorial.desired_patrols)) if territorial.needs_response else 0,
        int(exposure.combat_reserve_target),
    )
    low_tech_escort_doctrine=int(state.tech.weapons or 0)<LOW_TECH_ESCORT_WEAPONS
    if low_tech_escort_doctrine and phase_force_floor<=0 and not exposure.emergency_active:
        desired_combat=min(LOW_TECH_ESCORT_CAP,desired_combat)
    state.native['objective_combat_policy']={
        'phase':phase_name or 'unclassified',
        'phase_force_floor':phase_force_floor,
        'contact_only_deferral':contact_only_deferral,
        'low_tech_escort_doctrine':low_tech_escort_doctrine,
        'desired_combat':desired_combat,
        'execution_status':capability_status(state,design_capability_id('combat')),
        'blocked_native':capability_status(state,design_capability_id('combat')) == 'BLOCKED_NATIVE',
        'empire_exposure':exposure.to_dict(),
    }
    if d:=_pick(state,'combat'):
        combat_gap,generation_reason=_selected_role_gap(
            ds,counts,'combat',d,desired_combat,combat_assets,
        )
    else:
        combat_gap=0
        generation_reason=""
    if combat_gap and (d:=_pick(state,'combat')):
        req.append(BuildRequest(
            'combat',int(d['design_number']),d['name'],min(8 if exposure.emergency_active else 6,combat_gap),
            max(170, 190 if exposure.emergency_active else 0),
            f"{('phase='+phase_name+' requires a standing combat force' if phase_force_floor else 'foreign contact is established')} "
            f"(fleets={military_contact['foreign_fleet_count']}, planets={military_contact['foreign_planet_count']}); shared combat/escort hulls "
            f"live/imminent={combat_assets}, desired defensive force={desired_combat}; "
            f"territorial patrol objectives={territorial.desired_patrols}; empire exposure reserve="
            f"{exposure.combat_reserve_target} (pressure={exposure.sustained_pressure:.2f}, "
            f"recent losses={exposure.recent_planet_losses}); {generation_reason}."
            +(
                f" Low-tech escort doctrine holds the fleet at {LOW_TECH_ESCORT_CAP} hulls until "
                f"Weapons {LOW_TECH_ESCORT_WEAPONS}; research efficiency takes precedence over a mass early-fighter build."
                if low_tech_escort_doctrine else ""
            )
        ))

    req.sort(key=lambda r:r.priority,reverse=True)
    watchdog=_watchdog(state)
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
