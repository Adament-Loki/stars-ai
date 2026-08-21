from __future__ import annotations

from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path
import csv

from .fuel_planner import has_ife
from .colony_planner import colony_planet_is_eligible
from .ship_design_synth import plan_native_ship_design, synthesize_scout_upgrade
from .logistics_capacity import evaluate_logistics_capacity, POPULATION_PULSE_KT
from .starsapi_items import MINE_LAYER, hull_unlocked as race_hull_unlocked, stock_hulls as race_stock_hulls
from .territorial_defense import assess_territorial_defense

TECH_FIELDS=("energy","weapons","propulsion","construction","electronics","biotechnology")


def _unbuilt_superseded_combat_design(state) -> dict | None:
    """Return one safe obsolete military-design deletion, if any.

    Combat and escort are one shared design family.  A design that has neither
    a live hull nor a queue is superseded as soon as another family member is
    actually being built/used.  If none has reached production yet, retain only
    the newest candidate so a newly created design gets one turn of read-back
    before it is judged obsolete.
    """
    designs={
        int(d.get("design_number", -1)): d
        for d in (state.native or {}).get("designs", []) or []
        if not bool(d.get("is_starbase", False))
    }
    profiles={
        int(d.get("design_number", -1)): d
        for d in (state.native or {}).get("design_profiles", []) or []
        if str(d.get("role", "")) == "combat"
    }
    military_slots=sorted(slot for slot in profiles if slot in designs)
    if len(military_slots) < 2:
        return None

    live={slot:0 for slot in military_slots}
    for fleet in state.fleets:
        if fleet.owner != state.player_id:
            continue
        for slot,count in enumerate((fleet.native or {}).get("ship_count", []) or []):
            if slot in live:
                live[slot] += max(0, int(count or 0))
    queued={slot:0 for slot in military_slots}
    for queue in ((state.native or {}).get("production_by_planet", {}) or {}).values():
        for item in queue or []:
            if int(item.get("item_type", 0) or 0) != 4:
                continue
            slot=int(item.get("item_id", -1) or -1)
            if slot in queued:
                queued[slot] += max(0, int(item.get("count", 0) or 0))

    active={slot for slot in military_slots if live[slot] or queued[slot]}
    keep=set(active)
    if not keep:
        # No production evidence yet: retain exactly the most recently designed
        # candidate and reclaim older unbuilt variants one per native turn.
        keep.add(max(
            military_slots,
            key=lambda slot:(int(designs[slot].get("turn_designed", 0) or 0), slot),
        ))
    candidates=[
        slot for slot in military_slots
        if slot not in keep and live[slot] == 0 and queued[slot] == 0
        and int(designs[slot].get("total_remaining", 0) or 0) == 0
    ]
    if not candidates:
        return None
    slot=min(candidates,key=lambda candidate:(
        int(designs[candidate].get("turn_designed", 0) or 0), candidate,
    ))
    return {
        "target_slot":int(slot),
        "role":"combat",
        "design_name":str(profiles[slot].get("name") or designs[slot].get("name") or f"Design #{slot + 1}"),
        "reason":(
            "Delete unbuilt superseded shared combat/escort design: no live hulls, no queued builds, "
            "and a current family design is retained for both missions."
        ),
    }


@dataclass
class HullOption:
    hull_id:int
    name:str
    requirements:tuple[int,int,int,int,int,int]
    mass:int
    cargo:int
    fuel:int
    armor:int
    is_starbase:bool


@dataclass
class DesignProposal:
    role:str
    name:str
    is_starbase:bool
    desired_hull_id:int
    desired_hull_name:str
    desired_engine:str|None
    objectives:list[str]
    reason:str
    priority:int
    native_status:str="PENDING_NATIVE_DESIGN_CREATION_VALIDATION"

    def to_payload(self):
        return asdict(self)


@lru_cache(maxsize=1)
def stock_hulls()->dict[int,HullOption]:
    path=Path(__file__).with_name("data_hulls.mod")
    out={}
    for parts in csv.reader(path.read_text(encoding="latin-1").splitlines()):
        if not parts or int(parts[0]) not in (15,16):
            continue
        nums=[int(x) if x else 0 for x in parts[3:]]
        hid=int(nums[0])
        req=tuple(int(x) for x in nums[1:7])
        out[hid]=HullOption(
            hid,parts[2],req,int(nums[7]),int(nums[13]),int(nums[14]),
            int(nums[15]),int(parts[0])==16,
        )
    return out


def _unlocked(state,h:HullOption)->bool:
    """Whether the hull is researched *and* legal for this race's traits.

    The compact development view intentionally omits trait metadata.  Defer to
    the canonical StarsAPI-derived hull model here so PRT-only hulls such as
    the Mini Mine Layer are never proposed to an ineligible race.
    """
    canonical = race_stock_hulls().get(int(h.hull_id))
    return canonical is not None and race_hull_unlocked(canonical, state)


def _designs(state,role=None):
    ds=list(state.native.get("design_profiles",[]))
    if role is not None:
        ds=[d for d in ds if d.get("role")==role]
    return ds


def _best_unlocked_hull(state,ids):
    hs=[stock_hulls()[i] for i in ids if i in stock_hulls() and _unlocked(state,stock_hulls()[i])]
    return max(hs,key=lambda h:(h.cargo,h.fuel,h.armor,-h.mass),default=None)


def _has_engine(state,role,engine_id):
    return any(int(d.get("engine_id",-1))==int(engine_id) for d in _designs(state,role))


def plan_design_development(state,plan=None)->list[DesignProposal]:
    proposals=[]
    ife=has_ife(state.race)
    lrts={str(x).upper() for x in ((state.race.native or {}).get("lrts",[]) or [])}
    has_isb="ISB" in lrts
    fuel_mizer=(ife and int(state.tech.propulsion)>=2) or any(
        int(d.get("engine_id",-1))==2 for d in state.native.get("design_profiles",[])
    )
    preferred_engine="Fuel Mizer" if fuel_mizer else None

    scouts=_designs(state,"scout")
    unknown=sum(1 for p in state.planets if not p.observed)
    scout_upgrade=synthesize_scout_upgrade(state) if unknown>0 and scouts and fuel_mizer else None
    if scout_upgrade is not None:
        proposals.append(DesignProposal(
            role="scout", name="Long Range Scout Mk II", is_starbase=False,
            desired_hull_id=4, desired_hull_name="Scout", desired_engine="Fuel Mizer",
            objectives=[
                "materially improve actual scouting mission performance versus the best current scout",
                "retain the best currently available scanner",
                "do not accept a material Warp-7 range regression just to use Fuel Mizer",
            ],
            reason=scout_upgrade.reason,
            priority=125,
        ))

    freighters=_designs(state,"freighter")
    logistics=evaluate_logistics_capacity(state)
    # Keep design-development aligned with the execution doctrine. Opening
    # population movement wants compact long-range 200-kT carriers (Privateer
    # preferred once C4 is available), while Large Freighter is an industrial
    # bulk-mineral capability for mature/active shipyards.
    if freighters:
        compact=[d for d in freighters if POPULATION_PULSE_KT <= int(d.get("cargo_capacity",0) or 0) < 1000]
        bulk=[d for d in freighters if int(d.get("cargo_capacity",0) or 0) >= 1000]
        if logistics.desired_population_freighters>0:
            privateer=stock_hulls().get(11)
            medium=stock_hulls().get(1)
            desired=(privateer if privateer is not None and _unlocked(state,privateer) else medium)
            if desired is not None and _unlocked(state,desired):
                has_privateer_runner=any(
                    int(d.get("hull_id",-1))==11 and int(d.get("fuel_capacity",0) or 0)>=1200
                    for d in compact
                )
                needs_engine=fuel_mizer and not any(int(d.get("engine_id",-1))==2 for d in compact)
                if not compact or (desired.hull_id==11 and not has_privateer_runner) or needs_engine:
                    proposals.append(DesignProposal(
                        role="freighter", name="Onion Privateer", is_starbase=False,
                        desired_hull_id=desired.hull_id, desired_hull_name=desired.name,
                        desired_engine=preferred_engine,
                        objectives=[
                            "move one 20,000-colonist / 200-kT population pulse per source dispatch",
                            "on Privateer, use three basic Fuel Tanks for long-range loaded-out / empty-return cycling",
                            "favor Fuel Mizer or another proven efficient engine",
                            "reserve Large Freighters for bulk industrial mineral concentration",
                        ],
                        reason=(
                            f"Onion network wants {logistics.desired_population_freighters} compact population carrier(s); "
                            f"current compact carriers={len(compact)}. Preferred hull={desired.name}; Fuel Mizer available={fuel_mizer}."
                        ), priority=132,
                    ))
        if logistics.large_freighter_valuable and not bulk:
            large=stock_hulls().get(2)
            if large is not None and _unlocked(state,large):
                proposals.append(DesignProposal(
                    role="freighter", name="Bulk Freighter AI", is_starbase=False,
                    desired_hull_id=large.hull_id, desired_hull_name=large.name, desired_engine=preferred_engine,
                    objectives=[
                        "concentrate large I/B/G stockpiles at active fleet-construction shipyards",
                        "maximize bulk mineral throughput rather than routine population shuttling",
                        "retain fuel-safe range between industrial hubs",
                    ],
                    reason=(
                        f"Bulk industrial logistics pressure={logistics.bulk_transferable_kt} kT transferable; "
                        f"active shipyard builds={logistics.active_shipyard_build_count}; no >=1000-kT freighter exists."
                    ), priority=120,
                ))

    colonies=_designs(state,"colony")
    viable=sum(1 for p in state.planets if colony_planet_is_eligible(state,p,plan))
    if colonies and viable and fuel_mizer and not _has_engine(state,"colony",2):
        proposals.append(DesignProposal(
            role="colony", name="Colonizer Mk II", is_starbase=False,
            desired_hull_id=15 if 15 in stock_hulls() and _unlocked(state,stock_hulls()[15]) else 14,
            desired_hull_name="Colony Ship" if 15 in stock_hulls() and _unlocked(state,stock_hulls()[15]) else "Mini-Colony Ship",
            desired_engine="Fuel Mizer",
            objectives=["reach new colonies with a safe one-way fuel budget","carry the required colonization module","minimize nonessential mass"],
            reason=f"{viable} viable colony targets are known and Fuel Mizer improves colonization range.",
            priority=116,
        ))

    # Starbase hull availability is race-legal, not tech-only. Space Dock and
    # Ultra Station are Improved Starbases (ISB) LRT hulls and MUST NOT be
    # proposed for a non-ISB race.
    owned=[p for p in state.planets if p.owner==state.player_id]
    operational=sum(1 for p in owned if bool(((p.native or {}).get("starbase_capabilities") or {}).get("can_build_ships")))
    orbital_forts=sum(1 for p in owned if bool(((p.native or {}).get("starbase_capabilities") or {}).get("is_orbital_fort")))
    support_design_exists=any(
        bool((profile.get("capabilities") or {}).get("can_build_ships"))
        and bool((profile.get("capabilities") or {}).get("can_refuel"))
        for profile in (state.native or {}).get("starbase_profiles", []) or []
    )
    if not support_design_exists:
        # Every empire needs a custom support-base blueprint before the onion
        # network can promote P1/P2 worlds. ISB prefers Space Dock; all other
        # races use the universally legal Space Station. The compiler selects
        # only researched PRT/LRT-legal fittings and a free base-design slot.
        preferred_hull=33 if has_isb else 34
        support_hull=stock_hulls().get(preferred_hull)
        if support_hull is not None and _unlocked(state,support_hull):
            support_name="Fleet Support Space Dock" if preferred_hull==33 else "Fleet Support Space Station"
            proposals.append(DesignProposal(
                role="starbase", name=support_name, is_starbase=True,
                desired_hull_id=preferred_hull, desired_hull_name=support_hull.name, desired_engine=None,
                objectives=[
                    "provide real ship construction",
                    "provide orbital refueling",
                    "use an economical custom fitting so frontier P1/P2 worlds can fund it",
                    "leave later defensive or specialist starbase designs available when strategic need changes",
                ],
                reason=(
                    f"Empire has {operational} operational shipyard/refuel base(s), {orbital_forts} Orbital Fort(s), "
                    f"and no custom support-base blueprint. {support_hull.name} is the current legal support hull."
                ),
                priority=210 if operational==0 else 150,
            ))

    if has_isb and operational>0 and int(state.tech.construction)>=12:
        if not any(int(x.get("hull_id",-1))==35 for x in state.native.get("starbase_profiles",[])):
            proposals.append(DesignProposal(
                role="starbase", name="Ultra Station Support Base", is_starbase=True,
                desired_hull_id=35, desired_hull_name="Ultra Station", desired_engine=None,
                objectives=["increase orbital capacity and defenses","serve as a mature fleet-construction/refuel hub"],
                reason="ISB race with Construction 12 can consider an Ultra Station support design.",
                priority=95,
            ))

    # A minefield is a territorial claim and should be backed by a real
    # minelayer, not merely an advisory response. Design one when armed or
    # transport traffic enters claimed space and no existing minelayer design
    # can be built. Space Demolition gets the dedicated mine-layer hulls, but
    # all other races may use a compatible general-purpose hull with a
    # standard Mine Dispenser.
    territorial = assess_territorial_defense(state, plan)
    state.native["territorial_defense"] = territorial.to_dict()
    minelayers = _designs(state, "minelayer")
    if territorial.needs_minefield and not minelayers:
        minelayer_hull = next(
            (
                stock_hulls().get(hull_id)
                for hull_id in (28, 27, 5, 6, 7, 8, 10, 11, 13, 12, 29)
                if (
                    stock_hulls().get(hull_id) is not None
                    and _unlocked(state, stock_hulls()[hull_id])
                    and any(slot.allows(MINE_LAYER) for slot in race_stock_hulls()[hull_id].slots[1:])
                )
            ),
            None,
        )
        if minelayer_hull is not None:
            proposals.append(DesignProposal(
                role="minelayer", name="Territory Mine Layer", is_starbase=False,
                desired_hull_id=minelayer_hull.hull_id, desired_hull_name=minelayer_hull.name,
                desired_engine=None,
                objectives=[
                    "mark and defend violated territory with a minefield",
                    "fit a researched mine-laying component and an efficient legal engine",
                    "support patrol fleets rather than replacing them",
                ],
                reason=(
                    f"{territorial.reason} No minelayer design is available; create a legal "
                    f"{minelayer_hull.name} to establish territorial minefield coverage."
                ),
                priority=188,
            ))

    combat=_designs(state,"combat")
    if combat and max(int(state.tech.weapons),int(state.tech.energy))>=6:
        newest_turn=max((int(d.get("turn_designed",0) or 0) for d in combat),default=0)
        if state.year-2400-newest_turn>=5:
            proposals.append(DesignProposal(
                role="combat", name="Fleet Combat Mk II", is_starbase=False,
                desired_hull_id=6 if 6 in stock_hulls() and _unlocked(state,stock_hulls()[6]) else 5,
                desired_hull_name="Destroyer" if 6 in stock_hulls() and _unlocked(state,stock_hulls()[6]) else "Frigate",
                desired_engine=preferred_engine,
                objectives=["use newly unlocked Weapons/Energy technology","serve shared combat and escort duty","balance weapons, shields/armor, and mass"],
                reason=f"Weapons={state.tech.weapons}, Energy={state.tech.energy}; existing combat architecture is at least five turns old.",
                priority=190,
            ))

    proposals.sort(key=lambda x:x.priority,reverse=True)
    return proposals[:4]


def add_design_development_orders(state,orders,plan=None):
    # First-pass native Type27 execution is limited to exact utility designs.
    native_plan=plan_native_ship_design(state,plan)
    if native_plan is not None:
        payload=native_plan.to_payload()
        if native_plan.encoded.replace_existing:
            # Replacement is deliberately two-turn in v8.6.  Delete a provably
            # dead slot now; after the next M-file confirms it is free, the same
            # strategic need will synthesize the new design as create_ship_design.
            orders.add(
                "delete_ship_design", payload,
                native_plan.reason + " Delete dead design only this turn; create replacement after next-M read-back.",
                priority=native_plan.priority,
            )
        else:
            orders.add(
                "create_ship_design", payload, native_plan.reason,
                priority=native_plan.priority,
            )

    cleanup=None if native_plan is not None else _unbuilt_superseded_combat_design(state)
    if cleanup is not None:
        orders.add(
            "delete_ship_design",
            {"target_slot":cleanup["target_slot"],"role":cleanup["role"],"design_name":cleanup["design_name"]},
            cleanup["reason"] + " Native deletion is owner-aware and independently refuses any live, queued, or remaining hull.",
            priority=150,
        )

    for proposal in plan_design_development(state,plan):
        # Avoid duplicate advisory for the exact role already selected for this
        # turn's experimental native creation.
        if (native_plan is not None and proposal.role==native_plan.role) or cleanup is not None:
            continue
        orders.add("create_design",proposal.to_payload(),proposal.reason,priority=proposal.priority)
