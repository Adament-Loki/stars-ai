from __future__ import annotations

from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path
import csv

from .fuel_planner import has_ife
from .native.resources import native_resource_path
from .colony_planner import colony_planet_is_eligible
from .ship_design_synth import (
    COLONY_SHIP_HULL_ID,
    PRT_ALTERNATE_REALITY,
    best_legal_ship_hull,
    best_legal_starbase_hull,
    colony_design_records,
    plan_native_ship_design,
    ranked_legal_ship_hulls,
    synthesize_scout_upgrade,
    synthesize_starbase_design_proposal,
)
from .logistics_capacity import evaluate_logistics_capacity, POPULATION_PULSE_KT
from .starsapi_items import (
    MINE_LAYER,
    hull_unlocked as race_hull_unlocked,
    prt_id as race_prt_id,
    stock_hulls as race_stock_hulls,
)
from .diplomacy import allied_player_ids, world_faces_hostile
from .territorial_defense import assess_territorial_defense, hostile_colonies_inside_claimed_area
from .population_invasion import population_capture_targets, is_recently_lost_world
from .capability_execution import (
    BLOCKED_NATIVE,
    capability_status,
    design_capability_id,
    design_is_blocked,
    profile_is_blocked,
)

TECH_FIELDS=("energy","weapons","propulsion","construction","electronics","biotechnology")


def _design_activity(state, slots: set[int]) -> tuple[dict[int, int], dict[int, int]]:
    """Return owner-only live and queued counts for the given design slots."""
    live={slot:0 for slot in slots}
    for fleet in state.fleets:
        if fleet.owner != state.player_id:
            continue
        for slot,count in enumerate((fleet.native or {}).get("ship_count", []) or []):
            if slot in live:
                live[slot] += max(0, int(count or 0))
    queued={slot:0 for slot in slots}
    for queue in ((state.native or {}).get("production_by_planet", {}) or {}).values():
        for item in queue or []:
            if int(item.get("item_type", 0) or 0) != 4:
                continue
            slot=int(item.get("item_id", -1) or -1)
            if slot in queued:
                queued[slot] += max(0, int(item.get("count", 0) or 0))
    return live,queued


def _design_cargo_capacity(record: dict) -> int:
    if record.get("cargo_capacity") is not None:
        return max(0, int(record.get("cargo_capacity", 0) or 0))
    hull_id=record.get("hull_id")
    hull=race_stock_hulls().get(int(hull_id)) if hull_id is not None else None
    return max(0, int(hull.cargo if hull is not None else 0))


def _design_engine_id(record: dict) -> int | None:
    if record.get("engine_id") is not None:
        return int(record["engine_id"])
    for component in (record.get("slots", []) or []):
        if int(component.get("category", 0) or 0) == 1 and int(component.get("count", 0) or 0) > 0:
            return int(component.get("item_id", 0) or 0)
    return None


def _obsolete_colony_design(state) -> dict | None:
    """Return one safely deletable colony design that violates the new policy.

    Native design slots are stateful: a blueprint is deleted only when it has
    no live ships, no queued production, and the host reports no remaining
    hulls.  That lets us correct old non-cargo "colonizers" without risking a
    fleet that has not yet appeared in a partial M-file read-back.
    """
    raw={
        int(design.get("design_number", -1)): design
        for design in ((state.native or {}).get("designs", []) or [])
        if not bool(design.get("is_starbase", False))
    }
    records={
        int(record.get("design_number", -1)): record
        for record in colony_design_records(state)
        if int(record.get("design_number", -1)) in raw
    }
    if not records:
        return None
    live,queued=_design_activity(state,set(records))
    removable=[
        slot for slot in records
        if live[slot] == 0 and queued[slot] == 0
        and int(raw[slot].get("total_remaining", 0) or 0) == 0
    ]
    if not removable:
        return None

    # First remove historical designs that cannot perform their claimed
    # colonization mission, or that predate the dedicated-colony policy: a
    # non-cargo hull cannot deliver settlers, and general cargo hulls should
    # remain reusable transports rather than carrying a one-use module.
    non_cargo=[slot for slot in removable if _design_cargo_capacity(records[slot]) <= 0]
    invalid_ar_hull=[
        slot for slot in removable
        if race_prt_id(state) == PRT_ALTERNATE_REALITY
        and int(records[slot].get("hull_id", -1) or -1) != COLONY_SHIP_HULL_ID
    ]
    legacy_general_hull=[
        slot for slot in removable
        if int(records[slot].get("hull_id", -1) or -1) != COLONY_SHIP_HULL_ID
    ]
    invalid_colonizers=sorted(set(non_cargo) | set(invalid_ar_hull) | set(legacy_general_hull))
    if invalid_colonizers:
        slot=min(invalid_colonizers,key=lambda candidate:(
            int(raw[candidate].get("turn_designed", 0) or 0), candidate,
        ))
        reason=(
            "Delete legacy Alternate Reality colonizer on a non-Colony Ship hull."
            if slot in invalid_ar_hull else
            "Delete legacy non-cargo colonizer: a Colonization Module without cargo cannot deliver settlers."
            if slot in non_cargo else
            "Delete zero-ship general-hull colonizer: use the dedicated Colony Ship and retain freighters for repeat transport work."
        )
    elif len(records) > 2:
        # Bring legacy design inventories down to the two-architecture cap;
        # retain the largest/newest available designs while any live ships
        # continue to protect their own blueprints.
        slot=min(removable,key=lambda candidate:(
            _design_cargo_capacity(records[candidate]),
            int(raw[candidate].get("turn_designed", 0) or 0), candidate,
        ))
        reason="Delete excess colony architecture above the two-design policy cap."
    else:
        # A replacement is recognized only when the newer design actually
        # improves cargo capacity or changes the engine architecture.  This
        # preserves two genuinely complementary active designs while allowing
        # a superseded, zero-ship blueprint to be reclaimed.
        candidates=[]
        for slot in removable:
            older=records[slot]
            older_turn=int(raw[slot].get("turn_designed", 0) or 0)
            for newer_slot,newer in records.items():
                if newer_slot == slot:
                    continue
                newer_turn=int(raw[newer_slot].get("turn_designed", 0) or 0)
                if newer_turn < older_turn:
                    continue
                cargo_upgrade=_design_cargo_capacity(newer) > _design_cargo_capacity(older)
                engine_upgrade=_design_engine_id(newer) != _design_engine_id(older)
                if cargo_upgrade or engine_upgrade:
                    candidates.append(slot)
                    break
        if not candidates:
            return None
        slot=min(candidates,key=lambda candidate:(
            int(raw[candidate].get("turn_designed", 0) or 0), candidate,
        ))
        reason="Delete zero-ship colony design superseded by a newer cargo or engine architecture."
    return {
        "target_slot":int(slot),
        "role":"colony",
        "design_name":str(raw[slot].get("name") or f"Design #{slot + 1}"),
        "reason":reason,
    }


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

    # A live territorial response is precisely when a newly unlocked combat
    # blueprint needs time to reach an actual build queue.  Deleting it on the
    # next turn caused a create/delete loop in the playtest (slot 8 every
    # turn), consuming native design operations without ever fielding a hull.
    if assess_territorial_defense(state).needs_response:
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
    purpose:str|None=None
    native_status:str="PENDING_NATIVE_DESIGN_CREATION_VALIDATION"

    def to_payload(self):
        payload=asdict(self)
        payload["capability_id"]=design_capability_id(
            self.role,is_starbase=self.is_starbase,purpose=self.purpose,
        )
        return payload


@lru_cache(maxsize=1)
def stock_hulls()->dict[int,HullOption]:
    path=native_resource_path("mod", "data_hulls.mod")
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
    ds=[
        design for design in state.native.get("design_profiles",[])
        if not profile_is_blocked(state,design,is_starbase=False)
    ]
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
            compact_options=[
                hull for hull in ranked_legal_ship_hulls(
                    state,"freighter",minimum_cargo_kt=POPULATION_PULSE_KT,
                )
                if int(hull.cargo)<1000
            ]
            desired=min(
                compact_options,
                key=lambda hull:(
                    abs(int(hull.cargo)-POPULATION_PULSE_KT),
                    -int(hull.fuel),int(hull.engine_count),int(hull.hull_id),
                ),
                default=None,
            )
            if desired is not None:
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
            large=best_legal_ship_hull(state,"freighter")
            if large is not None and int(large.cargo)>=1000:
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

    # Pre-emptive population-capture transport capability.
    capture_targets=[
        target for target in population_capture_targets(state)
        if int(target.required_kt)>=800
    ]
    if capture_targets:
        needed_capture_capacity=min(int(target.required_kt) for target in capture_targets)
        best_existing_capture=max(
            (int(d.get("cargo_capacity",0) or 0) for d in freighters),
            default=0,
        )
        if best_existing_capture < needed_capture_capacity:
            capture_hull=best_legal_ship_hull(
                state,"freighter",minimum_cargo_kt=needed_capture_capacity,
            )
            if capture_hull is not None and int(capture_hull.cargo)>=needed_capture_capacity:
                recapture=False
                for target in capture_targets:
                    planet=next(
                        (p for p in state.planets if int(p.id)==int(target.planet_id)),
                        None,
                    )
                    if planet is not None and is_recently_lost_world(planet):
                        recapture=True
                        break
                proposals.append(DesignProposal(
                    role="freighter", name="Capture Transport AI", is_starbase=False,
                    desired_hull_id=capture_hull.hull_id,
                    desired_hull_name=capture_hull.name,
                    desired_engine=preferred_engine,
                    objectives=[
                        f"carry at least {needed_capture_capacity} kT population for a decisive capture packet",
                        "support guarded population capture without several small transport departures",
                        "retain fuel-safe range and remain useful as bulk transport outside capture operations",
                        "prefer a single legal large transport; validated fleet-merge groups remain a fallback for existing assets",
                    ],
                    reason=(
                        f"{len(capture_targets)} observed population-capture target(s) require 800+ kT; "
                        f"minimum required capacity={needed_capture_capacity} kT, best existing freighter design="
                        f"{best_existing_capture} kT. Seed capture capability before the assault planner needs it."
                    ),
                    priority=204 if recapture else 196,
                ))
        state.native["capture_transport_capability_demand"]={
            "observed_targets":len(capture_targets),
            "minimum_required_kt":needed_capture_capacity,
            "best_existing_design_kt":best_existing_capture,
            "large_transport_design_required":best_existing_capture < needed_capture_capacity,
            "policy":"Seed an 800+ kT capture transport when needed; execution retains breeder/range/diplomacy/AR safeguards.",
        }

    colonies=_designs(state,"colony")
    viable=sum(1 for p in state.planets if colony_planet_is_eligible(state,p,plan))
    colony_hull=best_legal_ship_hull(state,"colony")
    if (
        colonies and viable and fuel_mizer and not _has_engine(state,"colony",2)
        and colony_hull is not None
    ):
        proposals.append(DesignProposal(
            role="colony", name="Colonizer Mk II", is_starbase=False,
            desired_hull_id=colony_hull.hull_id,
            desired_hull_name=colony_hull.name,
            desired_engine="Fuel Mizer",
            objectives=["reach new colonies with a safe one-way fuel budget","carry the required colonization module","minimize nonessential mass"],
            reason=f"{viable} viable colony targets are known and Fuel Mizer improves colonization range.",
            priority=116,
        ))

    territorial = assess_territorial_defense(state, plan)
    state.native["territorial_defense"] = territorial.to_dict()

    # Starbase hull availability is race-legal, not tech-only. The selector
    # considers every stock base; it chooses the least costly hull that
    # actually supplies the current support-hub need.
    owned=[p for p in state.planets if p.owner==state.player_id]
    operational=sum(1 for p in owned if bool(((p.native or {}).get("starbase_capabilities") or {}).get("can_build_ships")))
    orbital_forts=sum(1 for p in owned if bool(((p.native or {}).get("starbase_capabilities") or {}).get("is_orbital_fort")))
    support_design_exists=any(
        bool((profile.get("capabilities") or {}).get("can_build_ships"))
        and bool((profile.get("capabilities") or {}).get("can_refuel"))
        for profile in (state.native or {}).get("starbase_profiles", []) or []
        if not profile_is_blocked(state,profile,is_starbase=True)
    )
    support_hull=best_legal_starbase_hull(state,purpose="support")
    if not support_design_exists:
        # Every empire needs a custom support-base blueprint before the onion
        # network can promote P1/P2 worlds. ISB prefers Space Dock; all other
        # races use the universally legal Space Station. The compiler selects
        # only researched PRT/LRT-legal fittings and a free base-design slot.
        if support_hull is not None:
            support_name=f"Fleet Support {support_hull.name}"
            proposals.append(DesignProposal(
                role="starbase", name=support_name, is_starbase=True,
                desired_hull_id=support_hull.hull_id, desired_hull_name=support_hull.name, desired_engine=None,
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
                purpose="support",
            ))

    operational_hull_ids = {
        int((planet.native or {}).get("starbase_capabilities", {}).get("hull_id", -1) or -1)
        for planet in owned
        if bool(((planet.native or {}).get("starbase_capabilities") or {}).get("can_build_ships"))
    }
    # A starter station can have a few basic weapons and still be strategically
    # obsolete.  Treating any nonzero weapon count as ``armed enough`` was the
    # reason research never produced a modern base: the old starter design
    # suppressed every same-hull refit forever.  Ask the canonical compiler
    # whether it can create a distinct legal current-tech variant instead. It
    # returns None for an exact architecture match, a full design namespace,
    # or a component/hull that is not actually legal, making this both a
    # capability check and a natural anti-duplicate gate.
    def _modern_variant(hull, *, purpose: str = "defense"):
        if hull is None:
            return None
        return synthesize_starbase_design_proposal(state, {
            "role": "starbase",
            "name": f"Modern {hull.name} {purpose.title()}",
            "is_starbase": True,
            "desired_hull_id": int(hull.hull_id),
            "desired_hull_name": hull.name,
            "purpose": purpose,
            "priority": 1,
        })

    # The current operational hull is the fastest refit target during an
    # emergency.  Outside an emergency it is still eligible for a one-at-a-
    # time modernization whenever research produces a stronger valid fitting.
    immediate_hull = next((
        best_legal_starbase_hull(state, purpose="defense", desired_hull_id=hull_id)
        for hull_id in sorted(operational_hull_ids)
        if best_legal_starbase_hull(state, purpose="defense", desired_hull_id=hull_id) is not None
    ), None)
    immediate_variant = _modern_variant(immediate_hull)

    # At Construction 12 or beyond, a larger legal orbital hull can be a
    # better normal modernization than a same-hull refit.  During an active
    # threat, however, prefer the current-hull refit whenever it exists: the
    # quicker overlap gives the threatened world protection sooner.
    advanced_hull = (
        best_legal_starbase_hull(state, purpose="defense")
        if operational > 0 and int(state.tech.construction) >= 12
        else None
    )
    advanced_variant = _modern_variant(advanced_hull)
    if territorial.needs_response:
        selected_hull, selected_variant, label, priority = (
            (immediate_hull, immediate_variant, "Immediate", 200)
            if immediate_variant is not None else
            (advanced_hull, advanced_variant, "Immediate Advanced", 196)
        )
    else:
        selected_hull, selected_variant, label, priority = (
            (advanced_hull, advanced_variant, "Advanced", 184)
            if advanced_variant is not None else
            (immediate_hull, immediate_variant, "Modernized", 176)
        )

    quiet_allied_border = bool(allied_player_ids(state)) and not any(
        world_faces_hostile(state, planet) for planet in owned
    )
    if selected_hull is not None and selected_variant is not None and not quiet_allied_border:
        proposals.append(DesignProposal(
            role="starbase", name=f"{label} {selected_hull.name} Defense", is_starbase=True,
            desired_hull_id=selected_hull.hull_id, desired_hull_name=selected_hull.name, desired_engine=None,
            objectives=[
                "replace starter orbital fittings with the best currently legal weapons and protection",
                "preserve existing orbital cover until the new defensive design completes",
                "queue one deliberate replacement after next-M design read-back",
            ],
            reason=(
                (
                    f"{territorial.reason} " if territorial.needs_response else
                    f"Weapons={state.tech.weapons}, Energy={state.tech.energy}, Construction={state.tech.construction}: "
                )
                + f"{selected_hull.name} has a distinct current-tech defensive architecture. "
                "The existing base remains active while the new design is created, read back, and queued as an overlapping replacement."
            ),
            priority=priority,
            purpose="defense",
        ))

    # A minefield is a territorial claim and should be backed by a real
    # minelayer, not merely an advisory response. Design one when armed or
    # transport traffic enters claimed space and no existing minelayer design
    # can be built. Space Demolition gets the dedicated mine-layer hulls, but
    # all other races may use a compatible general-purpose hull with a
    # standard Mine Dispenser.
    minelayers = _designs(state, "minelayer")
    if territorial.needs_minefield and not minelayers:
        minelayer_hull=best_legal_ship_hull(state,"minelayer")
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

    # Bomber capability for hostile colonies inside claimed territory.
    hostile_colonies=hostile_colonies_inside_claimed_area(state,plan)
    bombers=_designs(state,"bomber")
    if hostile_colonies and not bombers:
        bomber_hull=best_legal_ship_hull(state,"bomber")
        if bomber_hull is not None:
            proposals.append(DesignProposal(
                role="bomber", name="Territorial Strike Bomber", is_starbase=False,
                desired_hull_id=bomber_hull.hull_id,
                desired_hull_name=bomber_hull.name,
                desired_engine=preferred_engine,
                objectives=[
                    "carry researched bombs for hostile colonies inside claimed territory",
                    "support deliberate colony suppression/removal after military authorization",
                    "retain enough protection and range to operate with an escort fleet",
                ],
                reason=(
                    f"{len(hostile_colonies)} observed hostile/opposed colony world(s) lie inside the "
                    f"{territorial.claim_radius_ly:.0f}-ly claimed area; no bomber design exists. "
                    "Seed bombing capability before a territorial clearing campaign is ordered."
                ),
                priority=194,
            ))

    combat=_designs(state,"combat")
    combat_hull=best_legal_ship_hull(state,"combat")
    border_pressure=bool(
        territorial.needs_response
        or territorial.needs_minefield
        or territorial.desired_patrols>0
    )
    if not combat and border_pressure and combat_hull is not None:
        proposals.append(DesignProposal(
            role="combat", name="Border Combat Seed", is_starbase=False,
            desired_hull_id=combat_hull.hull_id,
            desired_hull_name=combat_hull.name,
            desired_engine=preferred_engine,
            objectives=[
                "create the empire's first real combat/escort architecture before a border incident becomes an emergency",
                "carry researched weapons plus legal protection rather than relying on armed scouts or utility hulls",
                "serve patrol, escort, and territorial interception roles from one seed design",
            ],
            reason=(
                f"{territorial.reason} Border pressure exists but the empire has no combat design. "
                "Create an explicit seed combat design instead of waiting for an existing combat family to upgrade."
            ),
            priority=202,
        ))
    elif (
        combat and max(int(state.tech.weapons),int(state.tech.energy))>=6
        and combat_hull is not None
    ):
        newest_turn=max((int(d.get("turn_designed",0) or 0) for d in combat),default=0)
        if state.year-2400-newest_turn>=5:
            proposals.append(DesignProposal(
                role="combat", name="Fleet Combat Mk II", is_starbase=False,
                desired_hull_id=combat_hull.hull_id,
                desired_hull_name=combat_hull.name,
                desired_engine=preferred_engine,
                objectives=["use newly unlocked Weapons/Energy technology","serve shared combat and escort duty","balance weapons, shields/armor, and mass"],
                reason=f"Weapons={state.tech.weapons}, Energy={state.tech.energy}; existing combat architecture is at least five turns old.",
                priority=190,
            ))

    state.native["proactive_force_capability_demand"]={
        "border_pressure":border_pressure,
        "combat_seed_required":bool(border_pressure and not combat),
        "hostile_colonies_in_claimed_area":[int(planet.id) for planet in hostile_colonies],
        "bomber_design_required":bool(hostile_colonies and not bombers),
        "minelayer_design_required":bool(territorial.needs_minefield and not minelayers),
        "desired_minelayers":int(territorial.desired_minelayers),
        "minefield_anchor_ids":list(territorial.uncovered_minefield_anchor_ids),
        "combat_execution_status":capability_status(state,design_capability_id("combat")),
        "bomber_execution_status":capability_status(state,design_capability_id("bomber")),
        "minelayer_execution_status":capability_status(state,design_capability_id("minelayer")),
    }

    proposals.sort(key=lambda x:x.priority,reverse=True)
    blocked=[]
    available=[]
    for proposal in proposals:
        capability_id=design_capability_id(
            proposal.role,is_starbase=proposal.is_starbase,purpose=proposal.purpose,
        )
        if design_is_blocked(
            state,proposal.role,is_starbase=proposal.is_starbase,purpose=proposal.purpose,
        ):
            blocked.append({
                "capability_id":capability_id,
                "role":proposal.role,
                "purpose":proposal.purpose,
                "priority":int(proposal.priority),
                "status":BLOCKED_NATIVE,
                "reason":proposal.reason,
            })
        else:
            available.append(proposal)
    state.native["blocked_native_design_capabilities"]=blocked
    state.native["open_design_capability_demand"]=blocked
    state.native["proactive_force_capability_demand"]["blocked_native_capabilities"]=[
        item["capability_id"] for item in blocked
    ]
    return available[:4]


def add_design_development_orders(state,orders,plan=None):
    # Repair a known-invalid colony blueprint before allocating another scarce
    # design operation.  The two-turn native read-back contract then makes the
    # next colonizer creation unambiguous.
    cleanup=_obsolete_colony_design(state)
    # A starbase modernization is an actual strategic replacement, not merely
    # an advisory second choice after an endless stream of utility hulls.  It
    # is still one Type-27 mutation and still waits for native read-back before
    # construction, but it may take that one design slot when its priority is
    # at least the competing native ship proposal.
    proposals=plan_design_development(state,plan)
    top_proposal=proposals[0] if proposals else None
    emergency_starbase=bool(
        top_proposal is not None
        and bool(top_proposal.is_starbase)
        and str(top_proposal.purpose or "").casefold() == "defense"
        and int(top_proposal.priority) >= 190
    )
    # Removing a bad unused colony blueprint is normally useful hygiene, but
    # it cannot consume the only Type-27 mutation while a threatened empire
    # needs an already-compilable base modernization. The cleanup remains
    # pending and is retried after the replacement design has been created.
    if cleanup is not None and emergency_starbase:
        cleanup=None
    native_plan=None if cleanup is not None else plan_native_ship_design(state,plan)
    if native_plan is not None and design_is_blocked(
        state,native_plan.role,is_starbase=native_plan.encoded.is_starbase,
    ):
        state.native.setdefault("open_design_capability_demand",[]).append({
            "capability_id":design_capability_id(
                native_plan.role,is_starbase=native_plan.encoded.is_starbase,
            ),
            "role":native_plan.role,
            "status":BLOCKED_NATIVE,
            "reason":"Native candidate suppressed because its prior Type-27 creation was blocked.",
        })
        native_plan=None
    prioritize_starbase=bool(
        top_proposal is not None
        and bool(top_proposal.is_starbase)
        and str(top_proposal.purpose or "").casefold() == "defense"
        and (native_plan is None or int(top_proposal.priority) >= int(native_plan.priority))
    )
    if prioritize_starbase:
        native_plan=None
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

    if cleanup is None and native_plan is None:
        cleanup=_unbuilt_superseded_combat_design(state)
    if cleanup is not None:
        orders.add(
            "delete_ship_design",
            {"target_slot":cleanup["target_slot"],"role":cleanup["role"],"design_name":cleanup["design_name"]},
            cleanup["reason"] + " Native deletion is owner-aware and independently refuses any live, queued, or remaining hull.",
            priority=150,
        )

    # Type-27 has one atomic native design mutation per turn.  Planning a
    # second advisory creation merely makes the writer discard it, obscuring
    # the real priority and delaying the next read-back.  Emit one selected
    # mutation only; the remaining needs are deterministically reconsidered
    # from the next M file.
    if native_plan is None and cleanup is None and top_proposal is not None:
        orders.add(
            "create_design",
            top_proposal.to_payload(),
            top_proposal.reason,
            priority=top_proposal.priority,
        )
