"""Conservative exact ship-design synthesis for first native Type27 attempts.

Initial native scope is intentionally narrow:
  * universal Scout hull upgrades;
  * universal Small/Medium/Large Freighter upgrades;
  * one legal identical engine type in the exact required engine count;
  * optional Scout equipment may be copied from an already-valid current Scout;
  * no armor-adding optional components, race-special hulls, combat optimization,
    colony module synthesis, or starbase design creation.
"""
from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .design_legality import (
    COLONIZATION_MODULE_ITEM_ID,
    COLONY_SHIP_HULL_ID,
    ORBITAL_CONSTRUCTION_MODULE_ITEM_ID,
    PRT_ALTERNATE_REALITY,
    ComponentCategory,
    ComponentRef,
    HULL_RULES,
    is_colonizer_module,
    validate_design,
)
from .expansion_network import evaluate_expansion_network
from .colony_planner import colony_planet_is_eligible
from .combat_doctrine import (
    BATTLE_COMPUTERS,
    ENERGY_CAPACITORS,
    calculate_offensive_rating,
    is_line_weapon,
    weapon_stats,
)
from .fuel_planner import ENGINE_DATA, best_range_ly, design_fuel_profile
from .starsapi_items import (
    stock_hulls as canonical_stock_hulls, hull_unlocked as canonical_hull_unlocked,
    component_spec, proven_available_components, remote_miner_package_ready,
    top_remote_miner_package, ENGINE, SCANNER, SHIELD, ARMOR, BEAM, TORPEDO,
    BOMB, MINING_ROBOT, MINE_LAYER, ORBITAL, ELECTRICAL, MECHANICAL,
)
from .logistics_capacity import evaluate_logistics_capacity, POPULATION_PULSE_KT
from .native.design_change import EncodedShipDesign, starbase_design_slot_safety
from .native.resources import native_resource_path
from .standard_mod import TechLevels
from .starbase_capabilities import starbase_capabilities

UNIVERSAL_FREIGHTER_HULLS = (0, 1, 2)  # Small, Medium, Large. Super Freighter is IS-specific.
MEDIUM_FREIGHTER_HULL_ID = 1
LARGE_FREIGHTER_HULL_ID = 2
SCOUT_HULL_ID = 4
PRIVATEER_HULL_ID = 11
FRIGATE_HULL_ID = 5
FUEL_MIZER_ITEM_ID = 2
FUEL_TANK_ITEM_ID = 5
FUEL_TANK_COUNT = 3
# A colony design is an expensive strategic architecture, not a disposable
# component carrier.  Two complementary cargo hulls (normally a compact and a
# long-range/bulk variant) cover the useful cases without consuming the ship
# design list with cosmetic variants.
MAX_COLONY_DESIGN_TYPES = 2
MAX_MINELAYER_DESIGN_TYPES = 2
MIN_NON_ENGINE_SLOT_UTILIZATION = 0.75


@dataclass(frozen=True)
class HullNativeSpec:
    hull_id: int
    name: str
    requirements: tuple[int, int, int, int, int, int]
    pic: int
    armor: int
    cargo: int
    fuel: int
    engine_count: int
    slot_count: int


@dataclass(frozen=True)
class NativeShipDesignPlan:
    encoded: EncodedShipDesign
    role: str
    priority: int
    reason: str
    confidence: str = "EXPERIMENTAL_TYPE27"

    def to_payload(self) -> dict:
        return {
            "target_slot": int(self.encoded.slot),
            "replace_existing": bool(self.encoded.replace_existing),
            "is_starbase": bool(self.encoded.is_starbase),
            "hull_id": int(self.encoded.hull_id),
            "pic": int(self.encoded.pic),
            "armor": int(self.encoded.armor),
            "turn_designed": int(self.encoded.turn_designed),
            "slots": [asdict(x) for x in self.encoded.slots],
            "name": self.encoded.name,
            "staging_name": self.encoded.staging_name or self.encoded.name,
            "role": self.role,
            "expected_signature": self.encoded.signature(),
            "native_status": self.confidence,
        }


@lru_cache(maxsize=1)
def stock_native_hulls() -> dict[int, HullNativeSpec]:
    return {
        hid: HullNativeSpec(
            hull_id=h.hull_id,name=h.name,requirements=h.tech_required,pic=h.pic,
            armor=h.armor,cargo=h.cargo,fuel=h.fuel,engine_count=h.engine_count,
            slot_count=h.slot_count,
        )
        for hid,h in canonical_stock_hulls().items() if not h.is_starbase
    }


@lru_cache(maxsize=1)
def stock_native_starbases() -> dict[int, HullNativeSpec]:
    """Return the stock starbase hulls in the same synthesis shape as ships."""
    return {
        hull_id: HullNativeSpec(
            hull_id=hull.hull_id, name=hull.name, requirements=hull.tech_required,
            pic=hull.pic, armor=hull.armor, cargo=hull.cargo, fuel=hull.fuel,
            engine_count=0, slot_count=hull.slot_count,
        )
        for hull_id, hull in canonical_stock_hulls().items()
        if hull.is_starbase
    }


@lru_cache(maxsize=1)
def _stock_starbase_hull_costs() -> dict[int, dict[str, int]]:
    """Read bare-hull costs for value-for-purpose starbase selection."""
    costs: dict[int, dict[str, int]] = {}
    path=native_resource_path("mod", "data_hulls.mod")
    for row in csv.reader(path.read_text(encoding="latin-1").splitlines()):
        if not row or int(row[0]) != 16:
            continue
        numbers=[int(value) if value else 0 for value in row[3:]]
        if len(numbers) < 12:
            continue
        costs[int(numbers[0])] = {
            "resources":int(numbers[8]),
            "ironium":int(numbers[9]),
            "boranium":int(numbers[10]),
            "germanium":int(numbers[11]),
        }
    return costs


def ranked_legal_starbase_hulls(
    state,
    *,
    purpose:str="support",
    desired_hull_id:int|None=None,
) -> list[HullNativeSpec]:
    """Return every researched, trait-legal stock base ranked for a mission.

    This is deliberately a full stock-hull search, not a Space Dock/Station
    allow-list.  A support hub favours the least expensive operational hull;
    a defensive request favours orbital capacity and protection.  The native
    compiler still validates every resulting fitting and design slot.
    """
    purpose=str(purpose).casefold()
    if desired_hull_id is not None:
        pool=[stock_native_starbases().get(int(desired_hull_id))]
    else:
        pool=list(stock_native_starbases().values())
    candidates=[]
    costs=_stock_starbase_hull_costs()
    for hull in pool:
        if hull is None or not _hull_unlocked(state,hull):
            continue
        capabilities=starbase_capabilities(hull.hull_id)
        if purpose in {"support","shipyard","refuel"} and not (
            capabilities["can_build_ships"] and capabilities["can_refuel"]
        ):
            continue
        cost=costs.get(hull.hull_id,{})
        total_cost=sum(max(0,int(cost.get(key,0))) for key in (
            "resources","ironium","boranium","germanium",
        ))
        if purpose in {"support","shipyard","refuel"}:
            # Small, cheap operational bases establish the expansion network
            # sooner.  Capability is a hard eligibility rule, not a proxy for
            # price, so this naturally picks a Space Dock for ISB but remains
            # open to any future legal stock base.
            rank=(total_cost,int(cost.get("resources",0)),hull.slot_count,hull.hull_id)
        else:
            rank=(-int(hull.armor),-int(hull.slot_count),total_cost,hull.hull_id)
        candidates.append((rank,hull))
    return [hull for _rank,hull in sorted(candidates,key=lambda row:row[0])]


def best_legal_starbase_hull(
    state,
    *,
    purpose:str="support",
    desired_hull_id:int|None=None,
) -> HullNativeSpec|None:
    """Return the best current legal stock starbase for the requested need."""
    ranked=ranked_legal_starbase_hulls(
        state,purpose=purpose,desired_hull_id=desired_hull_id,
    )
    return ranked[0] if ranked else None


def _tech(state) -> TechLevels:
    return TechLevels(
        energy=int(state.tech.energy or 0),
        weapons=int(state.tech.weapons or 0),
        propulsion=int(state.tech.propulsion or 0),
        construction=int(state.tech.construction or 0),
        electronics=int(state.tech.electronics or 0),
        biotechnology=int(state.tech.biotechnology or 0),
    )


def _hull_unlocked(state, hull: HullNativeSpec) -> bool:
    canonical=canonical_stock_hulls().get(int(hull.hull_id))
    return bool(canonical is not None and canonical_hull_unlocked(canonical,state))


def _lrts(state) -> set[str]:
    return {str(x).upper() for x in ((state.race.native or {}).get("lrts", []) or [])}


def _prt_id(state) -> int | None:
    value = ((getattr(state.race, "native", {}) or {}).get("prt_id"))
    if value is not None:
        return int(value)
    if "alternate reality" in str(getattr(state.race, "primary_trait", "") or "").casefold():
        return PRT_ALTERNATE_REALITY
    return None


def _staging_shipclass_name(state, hull_name: str) -> str:
    """Return the client-style temporary name for a new design.

    The final Type-27 record keeps its strategic/custom name.  The preceding
    empty staging record uses the stock ship class, e.g. ``Privateer``.  This
    matches the captured client-created `TestP` Privateer transaction.
    """
    return " ".join(str(hull_name or "Ship").split())


def _design_dicts(state) -> list[dict]:
    return [dict(x) for x in ((state.native or {}).get("designs", []) or []) if not x.get("is_starbase")]


def _fleet_slot_counts(state) -> dict[int, int]:
    out = {i: 0 for i in range(16)}
    for fleet in state.fleets:
        if fleet.owner != state.player_id:
            continue
        counts = (fleet.native or {}).get("ship_count", []) or []
        if isinstance(counts, int):
            continue
        for slot, count in enumerate(counts[:16]):
            out[slot] += int(count or 0)
    return out


def _queued_ship_slots(state) -> set[int]:
    out = set()
    for queue in ((state.native or {}).get("production_by_planet", {}) or {}).values():
        for item in queue or []:
            if int(item.get("item_type", 0) or 0) == 4:
                slot = int(item.get("item_id", -1) or -1)
                if 0 <= slot < 16 and int(item.get("count", 0) or 0) > 0:
                    out.add(slot)
    return out


def safe_recyclable_ship_slot(state, *, preferred_role: str | None = None) -> tuple[int, bool] | None:
    """Return (slot, replace_existing), never recycling a live/queued design."""
    designs = {int(d["design_number"]): d for d in _design_dicts(state)}
    live = _fleet_slot_counts(state)
    queued = _queued_ship_slots(state)
    # Free slots are safest: no delete required.
    for slot in range(16):
        if slot not in designs and live.get(slot, 0) == 0 and slot not in queued:
            return slot, False

    profiles = {int(d.get("design_number", -1)): d for d in ((state.native or {}).get("design_profiles", []) or [])}
    candidates = []
    for slot, design in designs.items():
        if live.get(slot, 0) > 0 or slot in queued:
            continue
        if int(design.get("total_remaining", 0) or 0) > 0:
            continue
        role = str(profiles.get(slot, {}).get("role", "unknown"))
        role_match = 0 if preferred_role and role == preferred_role else 1
        candidates.append((role_match, int(design.get("turn_designed", 0) or 0), slot))
    if not candidates:
        return None
    _, _, slot = min(candidates)
    return int(slot), True


def _design_by_number(state, slot: int) -> dict | None:
    return next((d for d in _design_dicts(state) if int(d.get("design_number", -1)) == int(slot)), None)


def _free_cruise_warp(profile: dict) -> int:
    eid = profile.get("engine_id")
    if eid not in ENGINE_DATA:
        return 0
    table = ENGINE_DATA[eid][2]
    out = 0
    for warp in range(1, min(9, len(table) - 1) + 1):
        if int(table[warp]) == 0:
            out = warp
    return out


def _best_profile(state, role: str) -> dict | None:
    profiles = [
        d for d in ((state.native or {}).get("design_profiles", []) or [])
        if d.get("role") == role
    ]
    if not profiles:
        return None
    if role == "freighter":
        return max(profiles, key=lambda d: (
            int(d.get("cargo_capacity", 0) or 0),
            int(d.get("fuel_capacity", 0) or 0),
            -int(d.get("dry_mass", 999999) or 999999),
        ))
    if role == "scout":
        ife = "IFE" in _lrts(state)
        # Scouting is currently routed aggressively at up to W7.  Pick the
        # actual best existing mission hull, not whichever scout happened to
        # appear first in the design-profile list.
        return max(profiles, key=lambda d: (
            best_range_ly(d, 7, ife),
            best_range_ly(d, 6, ife),
            _free_cruise_warp(d),
            float(d.get("fuel_capacity", 0) or 0) / max(1, int(d.get("dry_mass", 1) or 1)),
        ))
    return profiles[0]


def _engine_from_existing(state, role: str) -> int | None:
    profile = _best_profile(state, role)
    if profile is not None and profile.get("engine_id") is not None:
        return int(profile["engine_id"])
    for profile in ((state.native or {}).get("design_profiles", []) or []):
        if profile.get("engine_id") is not None:
            return int(profile["engine_id"])
    return None


def _engine_choice(state, role: str) -> int | None:
    if "IFE" in _lrts(state) and int(state.tech.propulsion or 0) >= 2:
        return FUEL_MIZER_ITEM_ID
    return _engine_from_existing(state, role)


def _validate_candidate(state, hull: HullNativeSpec, slots: tuple[ComponentRef, ...]) -> bool:
    # Physical legality comes from the one canonical hull model. Component
    # availability comes from the current research levels plus PRT/LRT gates.
    available=set(proven_available_components(state))
    result=validate_design(
        hull.hull_id,
        list(slots),
        hull_rules=HULL_RULES,
        available_components=available,
        prt_id=_prt_id(state),
    )
    return bool(result.ok)


def _empty_slot_array(hull: HullNativeSpec, engine_id: int) -> tuple[ComponentRef, ...]:
    slots = [ComponentRef(0, 0, 0) for _ in range(hull.slot_count)]
    slots[0] = ComponentRef(int(ComponentCategory.ENGINE), int(engine_id), int(hull.engine_count))
    return tuple(slots)


def _privateer_onion_slots(hull: HullNativeSpec, engine_id: int) -> tuple[ComponentRef, ...]:
    """Privateer + exact engine + three basic Fuel Tanks.

    Stock Privateer has one engine slot, one shield/armor slot, then three
    general slots that all accept Mechanical equipment. The basic Fuel Tank is
    Mechanical item id 5, has no tech requirement, mass 3, and adds 250 mg fuel.
    """
    slots=list(_empty_slot_array(hull,engine_id))
    if len(slots)<5:
        raise ValueError("Stock Privateer geometry unexpectedly has fewer than five slots")
    for i in (2,3,4):
        slots[i]=ComponentRef(int(ComponentCategory.MECHANICAL),FUEL_TANK_ITEM_ID,1)
    return tuple(slots)


def _has_onion_privateer(state) -> bool:
    for d in _design_dicts(state):
        if int(d.get("hull_id",-1))!=PRIVATEER_HULL_ID:
            continue
        tanks=0
        for raw in (d.get("slots") or []):
            cat=int(raw.get("category",0) if isinstance(raw,dict) else getattr(raw,"category",0))
            item=int(raw.get("item_id",0) if isinstance(raw,dict) else getattr(raw,"item_id",0))
            count=int(raw.get("count",0) if isinstance(raw,dict) else getattr(raw,"count",0))
            if cat==int(ComponentCategory.MECHANICAL) and item==FUEL_TANK_ITEM_ID:
                tanks+=count
        if tanks>=FUEL_TANK_COUNT:
            return True
    return False


def _compact_population_carrier_exists(state) -> bool:
    return any(
        d.get("role")=="freighter"
        and POPULATION_PULSE_KT <= int(d.get("cargo_capacity",0) or 0) < 1000
        for d in ((state.native or {}).get("design_profiles",[]) or [])
    )


def synthesize_onion_privateer(state) -> NativeShipDesignPlan | None:
    """Preferred opening 20k-population / hub-bootstrap round-trip carrier."""
    logistics=evaluate_logistics_capacity(state)
    network=evaluate_expansion_network(state)
    range_recovery_needed=bool((state.native or {}).get("colony_range_blocks"))
    if (
        logistics.population_lane_count<=0
        and not network.layer1_pending_ids
        and not range_recovery_needed
    ):
        return None
    hull=stock_native_hulls().get(PRIVATEER_HULL_ID)
    if hull is None or not _hull_unlocked(state,hull) or _has_onion_privateer(state):
        return None
    engine_id=_engine_choice(state,"freighter")
    if engine_id is None:
        return None
    target_slot=safe_recyclable_ship_slot(state,preferred_role="freighter")
    if target_slot is None:
        return None
    slot,replace=target_slot
    slots=_privateer_onion_slots(hull,engine_id)
    if not _validate_candidate(state,hull,slots):
        return None
    design=EncodedShipDesign(
        slot=slot,hull_id=hull.hull_id,pic=hull.pic,armor=hull.armor,
        turn_designed=max(0,int(state.year)-2400),slots=slots,
        name="Onion Privateer",staging_name=_staging_shipclass_name(state,hull.name),replace_existing=replace,
    )
    return NativeShipDesignPlan(
        design,"freighter",145,
        (
            f"Create dedicated onion transport on Privateer hull: {hull.engine_count} identical engine(s) "
            f"plus 3 basic Fuel Tanks. Stock cargo remains {hull.cargo} kT, enough for one "
            f"20,000-colonist / {POPULATION_PULSE_KT}-kT pulse with 50 kT spare; fuel rises from "
            f"{hull.fuel} to about {hull.fuel+3*250} mg. This compact long-range carrier supports "
            "repeated loaded-out/empty-return micro without forcing early Large-Freighter research. "
            + (
                "Known colony assets are range-blocked, so this same long-range tank architecture is also retained as a co-located merge-and-return recovery tender. "
                if range_recovery_needed else ""
            )
            + f"Slot {slot} is {'dead/recyclable' if replace else 'free'}; production waits for next-M read-back."
        ),
    )


def synthesize_medium_population_transport(state) -> NativeShipDesignPlan | None:
    """Create the best current compact carrier for a 200-kT population lane."""
    logistics=evaluate_logistics_capacity(state)
    if logistics.desired_population_freighters<=0 or _compact_population_carrier_exists(state):
        return None
    engine_id=_engine_choice(state,"freighter")
    if engine_id is None:
        return None
    compact_hulls=sorted(
        (
            hull for hull in ranked_legal_ship_hulls(
                state,"freighter",minimum_cargo_kt=POPULATION_PULSE_KT,
            )
            if hull.cargo < 1000
        ),
        key=lambda hull:(
            abs(int(hull.cargo)-POPULATION_PULSE_KT),
            -int(hull.fuel),int(hull.engine_count),int(hull.hull_id),
        ),
    )
    for hull in compact_hulls:
        # The generic freight ranking favours the greatest useful hold.  For
        # this opening-specific fallback, a compact hull near the current
        # 200-kT need is more economical than a premature bulk transport.
        target_slot=safe_recyclable_ship_slot(state,preferred_role="freighter")
        if target_slot is None:
            return None
        slot,replace=target_slot
        slots=_empty_slot_array(hull,engine_id)
        if not _validate_candidate(state,hull,slots):
            continue
        design=EncodedShipDesign(
            slot=slot,hull_id=hull.hull_id,pic=hull.pic,armor=hull.armor,
            turn_designed=max(0,int(state.year)-2400),slots=slots,
            name="Population Shuttle",staging_name=_staging_shipclass_name(state,hull.name),replace_existing=replace,
        )
        return NativeShipDesignPlan(
            design,"freighter",133,
            f"No verified compact {POPULATION_PULSE_KT}-kT population carrier exists. "
            f"Create {hull.name} for phased 20k-colonist onion pulses; slot {slot} "
            f"is {'dead/recyclable' if replace else 'free'}. Production waits for read-back.",
        )
    return None


def synthesize_freighter_upgrade(state) -> NativeShipDesignPlan | None:
    """Create the best current bulk carrier when industrial work justifies it."""
    logistics=evaluate_logistics_capacity(state)
    if not logistics.large_freighter_valuable:
        return None
    current=_best_profile(state,"freighter")
    current_cargo=max(
        (int(d.get("cargo_capacity",0) or 0) for d in ((state.native or {}).get("design_profiles",[]) or []) if d.get("role")=="freighter"),
        default=0,
    )
    engine_id=_engine_choice(state,"freighter")
    if engine_id is None:
        return None
    for hull in ranked_legal_ship_hulls(state,"freighter"):
        if hull.cargo <= current_cargo:
            continue
        target_slot=safe_recyclable_ship_slot(state,preferred_role="freighter")
        if target_slot is None:
            return None
        slot,replace=target_slot
        slots=_empty_slot_array(hull,engine_id)
        if not _validate_candidate(state,hull,slots):
            continue
        design=EncodedShipDesign(
            slot=slot,hull_id=hull.hull_id,pic=hull.pic,armor=hull.armor,
            turn_designed=max(0,int(state.year)-2400),slots=slots,
            name="Bulk Freighter AI",staging_name=_staging_shipclass_name(state,hull.name),replace_existing=replace,
        )
        return NativeShipDesignPlan(
            design,"freighter",140,
            (
                f"Create {hull.name} for BULK industrial logistics, not routine population movement: "
                f"transferable mineral pressure={logistics.bulk_transferable_kt} kT, active shipyard builds="
                f"{logistics.active_shipyard_build_count}. Hull requires {hull.engine_count} identical engine(s); "
                f"base cargo={hull.cargo} kT. Slot {slot} is {'dead/recyclable' if replace else 'free'}. "
                "Production waits for next-M read-back."
            ),
        )
    return None


def synthesize_scout_upgrade(state) -> NativeShipDesignPlan | None:
    """Create a scout only when it materially beats the best existing scout.

    v8.5 treated "uses Fuel Mizer" as synonymous with "better scout".  That
    is false for aggressive W7 recon: a Daddy Long Legs 7 design can have more
    W7 range than the otherwise lighter Fuel-Mizer clone.  v8.6 therefore
    compares the candidate to the best current scout before consuming a slot.
    """
    if "IFE" not in _lrts(state) or int(state.tech.propulsion or 0) < 2:
        return None
    current_profile = _best_profile(state, "scout")
    if current_profile is None:
        return None
    hull = stock_native_hulls().get(SCOUT_HULL_ID)
    if hull is None or not _hull_unlocked(state, hull):
        return None
    current_design = _design_by_number(state, int(current_profile["design_number"]))
    if current_design is None:
        return None
    raw_slots = list(current_design.get("slots", []) or [])
    if len(raw_slots) != hull.slot_count:
        return None
    slots = [ComponentRef(0, 0, 0) for _ in range(hull.slot_count)]
    slots[0] = ComponentRef(int(ComponentCategory.ENGINE), FUEL_MIZER_ITEM_ID, hull.engine_count)
    for i in range(1, hull.slot_count):
        raw = raw_slots[i]
        cat = int(raw.get("category", 0) if isinstance(raw, dict) else getattr(raw, "category", 0))
        item = int(raw.get("item_id", 0) if isinstance(raw, dict) else getattr(raw, "item_id", 0))
        count = int(raw.get("count", 0) if isinstance(raw, dict) else getattr(raw, "count", 0))
        if count <= 0 or cat == int(ComponentCategory.ARMOR):
            continue
        slots[i] = ComponentRef(cat, item, count)
    slots_t = tuple(slots)
    if not _validate_candidate(state, hull, slots_t):
        return None

    candidate_profile = design_fuel_profile({
        "design_number": 0, "name": "Long Range Scout II", "hull_id": hull.hull_id,
        "slots": [asdict(x) for x in slots_t],
    }, role="scout").to_dict()
    ife = "IFE" in _lrts(state)
    current_w7 = best_range_ly(current_profile, 7, ife)
    candidate_w7 = best_range_ly(candidate_profile, 7, ife)
    current_w6 = best_range_ly(current_profile, 6, ife)
    candidate_w6 = best_range_ly(candidate_profile, 6, ife)
    current_free = _free_cruise_warp(current_profile)
    candidate_free = _free_cruise_warp(candidate_profile)

    # Do not burn a scarce design slot for a nominal engine upgrade that makes
    # our actual aggressive scouting mission materially worse.
    if current_w7 > 0 and candidate_w7 < current_w7 * 0.95:
        return None
    materially_better = bool(
        candidate_w7 >= current_w7 * 1.12
        or candidate_w6 >= current_w6 * 1.12
        or (candidate_free >= current_free + 2 and candidate_w7 >= current_w7 * 0.98)
    )
    if not materially_better:
        return None

    target_slot = safe_recyclable_ship_slot(state, preferred_role="scout")
    if target_slot is None:
        return None
    slot, replace = target_slot
    design = EncodedShipDesign(
        slot=slot, hull_id=hull.hull_id, pic=hull.pic, armor=hull.armor,
        turn_designed=max(0, int(state.year) - 2400), slots=slots_t,
        name="Long Range Scout II", staging_name=_staging_shipclass_name(state,hull.name), replace_existing=replace,
    )
    return NativeShipDesignPlan(
        design, "scout", 128,
        (
            f"Create scout only after mission comparison versus best current scout: "
            f"W7 range {candidate_w7:.0f} vs {current_w7:.0f} ly, W6 {candidate_w6:.0f} vs "
            f"{current_w6:.0f} ly, free cruise W{candidate_free} vs W{current_free}. "
            f"Slot {slot} is {'dead/recyclable' if replace else 'free'}; any dead-slot recycle is "
            "delete-only this turn and creation waits for next-M read-back."
        ),
    )



ROLE_CATEGORY_WEIGHTS={
    "scout":{SCANNER:12.0,ELECTRICAL:3.0,SHIELD:2.0,ARMOR:1.0},
    "combat":{BEAM:9.0,TORPEDO:9.0,SHIELD:6.0,ARMOR:5.0,ELECTRICAL:4.0,MECHANICAL:1.0,SCANNER:.5},
    "bomber":{BOMB:12.0,SHIELD:4.0,ARMOR:3.0,ELECTRICAL:2.0,MECHANICAL:1.0},
    # Remote mining stays a dedicated planetary-mining architecture.  It may
    # carry protection and range support, but never consumes combat fittings.
    "miner":{MINING_ROBOT:12.0,SHIELD:3.0,ARMOR:2.0,ELECTRICAL:2.0,MECHANICAL:1.0},
    # A mine layer may fill weapon slots for self-defence, but mine capacity
    # dominates its hull choice.  This avoids an under-filled cruiser when a
    # Frigate can carry more dispensers for far fewer resources.
    "minelayer":{MINE_LAYER:18.0,SHIELD:4.0,ARMOR:3.0,ELECTRICAL:2.0,SCANNER:2.0,MECHANICAL:1.0,BEAM:2.0,TORPEDO:1.0},
}

ROLE_DEFINING_CATEGORIES={
    "scout":(SCANNER,),
    "combat":(BEAM,TORPEDO),
    "bomber":(BOMB,),
    "miner":(MINING_ROBOT,),
    "minelayer":(MINE_LAYER,),
    "colony":(MECHANICAL,),
}


def _hull_slot_value(hull:HullNativeSpec, role:str) -> float:
    """Mission usefulness from legal slot geometry, before fitting parts."""
    canonical=canonical_stock_hulls()[hull.hull_id]
    weights=ROLE_CATEGORY_WEIGHTS.get(role,{})
    fitting_value=sum(
        int(slot.capacity)*max(
            (float(weight) for category,weight in weights.items() if slot.allows(category)),
            default=0.0,
        )
        for slot in canonical.slots[1:]
    )
    if role=="freighter":
        return 8.0*float(hull.cargo)+0.75*float(hull.fuel)-0.20*float(hull.armor)
    if role=="colony":
        # Colonization is a single-use capability.  Once a world is founded,
        # transporting the next settlement wave is a separate recurring
        # logistics job, so cargo capacity must not make a Large Freighter
        # with a module beat the inexpensive dedicated Colony Ship.
        if int(hull.hull_id) == COLONY_SHIP_HULL_ID:
            return 10_000.0
        colony_slots=sum(
            int(slot.capacity) for slot in canonical.slots[1:]
            if slot.allows(MECHANICAL)
        )
        return 12.0*colony_slots+0.10*float(hull.cargo)+0.50*float(hull.fuel)-0.10*float(hull.armor)
    if role=="minelayer":
        # Mine capacity is the actual role output.  It must dominate broad
        # empty weapon slots and cargo, otherwise a Cruiser with two mine
        # dispensers displaced a Frigate that fits three dispensers and every
        # support slot.  Cargo is still a strong negative: transport and mine
        # layer are distinct architectures.
        mine_capacity=sum(
            int(slot.capacity) for slot in canonical.slots[1:]
            if slot.allows(MINE_LAYER)
        )
        return (
            100.0*mine_capacity
            +fitting_value+0.20*float(hull.armor)+0.04*float(hull.fuel)
            -0.20*float(hull.cargo)
        )
    return fitting_value+0.20*float(hull.armor)+0.04*float(hull.fuel)


def ranked_legal_ship_hulls(
    state,
    role:str,
    *,
    desired_hull_id:int|None=None,
    minimum_cargo_kt:int=0,
) -> list[HullNativeSpec]:
    """Rank all researched race-legal stock ship hulls that fit a role.

    We do not maintain a small curated hull allow-list here.  A hull becomes a
    candidate when it is legal for the race, has its required engine geometry,
    has enough cargo for a freight request when applicable, and can hold the
    role's defining component.  Final component/slot validation still happens
    during synthesis.
    """
    role=str(role).casefold()
    # AR colonizes with the Orbital Construction Module, and only on the
    # Colony Ship.  General cargo ships may still carry colonists, but cannot
    # become colony ships merely because they expose a Mechanical slot.
    if (
        role == "colony"
        and _prt_id(state) == PRT_ALTERNATE_REALITY
        and desired_hull_id is not None
        and int(desired_hull_id) != COLONY_SHIP_HULL_ID
    ):
        return []
    # Dedicated minefield doctrine starts with the Frigate: its three
    # dispenser positions make it the first genuinely cost-effective general
    # mine layer.  Before that, preserve scarce early design slots and
    # resources for scouts, colonizers, and transport rather than fielding an
    # underpowered Destroyer workaround.
    if role == "minelayer":
        frigate=stock_native_hulls().get(FRIGATE_HULL_ID)
        if frigate is None or not _hull_unlocked(state,frigate):
            return []
    if desired_hull_id is not None:
        pool=[stock_native_hulls().get(int(desired_hull_id))]
    else:
        pool=list(stock_native_hulls().values())
    defining=ROLE_DEFINING_CATEGORIES.get(role,())
    candidates=[]
    for hull in pool:
        if hull is None or not _hull_unlocked(state,hull):
            continue
        if (
            role == "colony"
            and _prt_id(state) == PRT_ALTERNATE_REALITY
            and int(hull.hull_id) != COLONY_SHIP_HULL_ID
        ):
            continue
        if int(hull.cargo)<int(minimum_cargo_kt):
            continue
        canonical=canonical_stock_hulls()[hull.hull_id]
        if not canonical.slots or not canonical.slots[0].allows(ENGINE):
            continue
        if defining and not any(
            slot.allows(category)
            for slot in canonical.slots[1:]
            for category in defining
        ):
            continue
        # Higher mission utility wins.  With equal utility prefer fewer
        # engines/mass, then deterministic hull ID ordering.
        rank=(
            -_hull_slot_value(hull,role),
            int(hull.engine_count),
            int(hull.armor),
            int(hull.hull_id),
        )
        candidates.append((rank,hull))
    # A dedicated mine layer is not a cargo/mine hybrid.  When a legal
    # zero-cargo combat hull exists, prefer that family even if a Privateer
    # happens to fit one more dispenser: the latter remains available for its
    # distinct transport role.  This yields the inexpensive Destroyer before
    # Frigate research and the three-dispenser Frigate thereafter.
    if role == "minelayer" and any(int(hull.cargo) == 0 for _rank,hull in candidates):
        candidates=[(rank,hull) for rank,hull in candidates if int(hull.cargo) == 0]
    return [hull for _rank,hull in sorted(candidates,key=lambda row:row[0])]


def best_legal_ship_hull(
    state,
    role:str,
    *,
    desired_hull_id:int|None=None,
    minimum_cargo_kt:int=0,
) -> HullNativeSpec|None:
    """Return the top legal stock hull for a concrete ship mission."""
    ranked=ranked_legal_ship_hulls(
        state,role,desired_hull_id=desired_hull_id,
        minimum_cargo_kt=minimum_cargo_kt,
    )
    return ranked[0] if ranked else None


def _component_pool_for_role(
    state,
    role:str,
    *,
    combat_weapon_category:int|None=None,
):
    """Return researched fittings compatible with the requested architecture.

    A standard combat line chooses one weapon family before individual slots
    are populated.  That prevents an accidental beam/torpedo hybrid merely
    because two slot masks happen to differ.  Sappers and mine munitions stay
    out of the ordinary anti-ship line; a future dedicated sapper role can
    deliberately request them instead.
    """
    proven=proven_available_components(state)
    weights=ROLE_CATEGORY_WEIGHTS.get(role,{})
    return [
        spec for spec in proven.values()
        if spec.category in weights
        and not (
            role == "minelayer" and spec.category != MINE_LAYER
        )
        and not (
            role == "combat"
            and spec.category in (BEAM,TORPEDO)
            and (
                not is_line_weapon(spec.category,spec.item_id)
                or (
                    combat_weapon_category is not None
                    and spec.category != int(combat_weapon_category)
                )
            )
        )
    ]


def _combat_weapon_slot_rating(canonical_hull, engine_id: int, component, count: int) -> float:
    """Rate one prospective combat weapon in the actual ship-board context.

    Category weight decides whether a slot is used for offense, defense, or a
    mission component. Once two weapons are equally appropriate for a combat
    slot, native item IDs are not a meaningful measure of battle value. Build
    the smallest representative fitting instead: the selected hull, its
    required engines, and this weapon at the slot's real capacity. This makes
    weapon damage, range, firing initiative, torpedo accuracy, and the weapon
    mass contribution to board movement visible to the selection.
    """
    if int(component.category) not in (BEAM, TORPEDO):
        return 0.0
    engine = component_spec(ENGINE, int(engine_id))
    copies = max(1, int(count))
    mass = (
        int(canonical_hull.mass)
        + int(engine.mass) * int(canonical_hull.engine_count)
        + int(component.mass) * copies
    )
    return calculate_offensive_rating({
        "hull_id": int(canonical_hull.hull_id),
        "mass": mass,
        "slots": [
            {"category": ENGINE, "item_id": int(engine_id), "count": int(canonical_hull.engine_count)},
            {"category": int(component.category), "item_id": int(component.item_id), "count": copies},
        ],
    }).rating


def _role_component_choice_key(
    role: str,
    canonical_hull,
    engine_id: int,
    slot,
    component,
    *,
    combat_weapon_category:int|None=None,
    enemy_torpedo_pressure:float=0.0,
) -> tuple[float, float, int, int]:
    """Return a deterministic, mission-aware ordering for one legal fitting."""
    role = str(role).casefold()
    category_weight=float(ROLE_CATEGORY_WEIGHTS.get(role, {}).get(component.category, 0.0))
    if role == "combat" and int(component.category) == ELECTRICAL:
        if str(getattr(component,"name","")).startswith("Jammer ") and enemy_torpedo_pressure > 0:
            # The exact enemy combat report is evidence, unlike generic
            # caution. Retain one jammer before optional beam acceleration.
            category_weight=9.0
        elif int(combat_weapon_category or 0) == BEAM:
            # One capacitor is a meaningful beam-line force multiplier; a
            # computer remains useful for initiative after that first cap.
            if int(component.item_id) in ENERGY_CAPACITORS:
                category_weight=8.0
            elif int(component.item_id) in BATTLE_COMPUTERS:
                category_weight=7.0
        elif int(combat_weapon_category or 0) == TORPEDO:
            category_weight=8.0 if int(component.item_id) in BATTLE_COMPUTERS else 3.0
    return (
        category_weight,
        _combat_weapon_slot_rating(canonical_hull, engine_id, component, slot.capacity)
        if role == "combat" else 0.0,
        int(component.item_id),
        -int(component.mass),
    )


_MOBILITY_COMPONENT_IDS = frozenset({7, 8})  # Maneuvering Jet, Overthruster


def _combat_design_offensive_rating(canonical_hull, slots: list[ComponentRef]) -> float:
    """Evaluate the fitted design, including support-component synergies."""
    mass = int(canonical_hull.mass)
    for fitted in slots:
        if int(fitted.count) <= 0:
            continue
        mass += int(component_spec(fitted.category, fitted.item_id).mass) * int(fitted.count)
    return calculate_offensive_rating({
        "hull_id": int(canonical_hull.hull_id),
        "mass": mass,
        "slots": [asdict(fitted) for fitted in slots],
    }).rating


def _weapon_count(slots: list[ComponentRef]) -> int:
    return sum(
        int(fitted.count) for fitted in slots
        if int(fitted.category) in (BEAM, TORPEDO) and int(fitted.count) > 0
    )


def _refit_combat_support_components(
    canonical_hull,
    slots: list[ComponentRef],
    available_components,
    *,
    combat_weapon_category:int|None=None,
    enemy_torpedo_pressure:float=0.0,
) -> list[ComponentRef]:
    """Fit computer and mobility support only when it improves the whole ship.

    A Battle Computer/Nexus is not independently an offensive item: its value
    comes from every torpedo's improved hit chance and every weapon's earlier
    initiative. Likewise, a jet or overthruster matters only if it changes the
    board movement after hull, engine, and component mass are included. This
    bounded hill-climb therefore evaluates a prospective replacement against
    the complete design instead of assigning a static category score.

    It may exchange a weapon-capable general slot for a computer if the full
    design improves, but never spends the final weapon or removes an existing
    shield/armor fitting merely to inflate an offensive-only metric.
    """
    result = list(slots)
    support_components = [
        component for component in available_components
        if (
            int(component.category) == ELECTRICAL
            and (
                int(component.item_id) in BATTLE_COMPUTERS
                or (
                    int(combat_weapon_category or 0) == BEAM
                    and int(component.item_id) in ENERGY_CAPACITORS
                )
            )
        ) or (
            int(component.category) == MECHANICAL
            and int(component.item_id) in _MOBILITY_COMPONENT_IDS
        )
    ]
    if not support_components or _weapon_count(result) <= 0:
        return result
    dedicated_computer_capacity=sum(
        int(slot.capacity) for slot in canonical_hull.slots[1:]
        if int(slot.allowed_categories) == ELECTRICAL
    )

    # There are at most 16 design slots. Strictly improving replacements make
    # this finite even when a flexible slot can hold several component types.
    for _ in range(max(1, len(canonical_hull.slots) * len(support_components))):
        baseline = _combat_design_offensive_rating(canonical_hull, result)
        best: tuple[float, int, object, list[ComponentRef]] | None = None
        for slot in canonical_hull.slots[1:]:
            current = result[int(slot.index)]
            # Do not trade durable protection for a scalar offense increase.
            if (
                int(current.count) > 0
                and (
                    int(current.category) in (SHIELD, ARMOR)
                    or (
                        enemy_torpedo_pressure > 0
                        and int(current.category) == ELECTRICAL
                        and int(current.item_id) not in BATTLE_COMPUTERS
                        and int(current.item_id) not in ENERGY_CAPACITORS
                    )
                )
            ):
                continue
            for component in support_components:
                if not slot.allows(component.category):
                    continue
                if (
                    int(combat_weapon_category or 0) == TORPEDO
                    and dedicated_computer_capacity > 0
                    and int(slot.allowed_categories) != ELECTRICAL
                    and int(component.category) == ELECTRICAL
                    and int(component.item_id) in BATTLE_COMPUTERS
                ):
                    continue
                # A single capacitor is the economical standard beam package.
                # Further copies may be tactically valid, but require an
                # explicit high-end/counter-design request instead of silently
                # displacing every defensive component in a general fitter.
                if (
                    int(component.item_id) in ENERGY_CAPACITORS
                    and any(
                        int(fitted.category) == ELECTRICAL
                        and int(fitted.item_id) in ENERGY_CAPACITORS
                        for fitted in result
                    )
                    and not (
                        int(current.category) == ELECTRICAL
                        and int(current.item_id) in ENERGY_CAPACITORS
                    )
                ):
                    continue
                candidate = list(result)
                candidate[int(slot.index)] = ComponentRef(
                    int(component.category), int(component.item_id), max(1, int(slot.capacity)),
                )
                if _weapon_count(candidate) <= 0:
                    continue
                candidate_rating = _combat_design_offensive_rating(canonical_hull, candidate)
                if candidate_rating <= baseline + 1e-9:
                    continue
                tie_break = (
                    candidate_rating,
                    int(component.category) == ELECTRICAL,
                    int(component.item_id),
                    -int(component.mass),
                )
                if best is None or tie_break > (
                    best[0],
                    int(best[2].category) == ELECTRICAL,
                    int(best[2].item_id),
                    -int(best[2].mass),
                ):
                    best = (candidate_rating, int(slot.index), component, candidate)
        if best is None:
            break
        result = best[3]
    return result


_COMBAT_ROLE_ALIASES={
    "beam_interceptor":BEAM,
    "beam_line":BEAM,
    "torpedo_line":TORPEDO,
    "capital_missile":TORPEDO,
}


def _observed_weapon_pressure(state) -> tuple[float,float]:
    """Return observed (beam, torpedo) pressure without guessing unseen gear."""
    rows=[]
    for key in ("enemy_design_profiles","enemy_combat_profiles","enemy_designs"):
        rows.extend((getattr(state,"native",{}) or {}).get(key,[]) or [])
    beam=torpedo=0.0
    for row in rows:
        read=(lambda name, default=0: row.get(name,default)) if isinstance(row,dict) else (lambda name,default=0: getattr(row,name,default))
        beam += float(read("beam_strength",0) or 0)
        torpedo += float(read("torpedo_strength",0) or 0)
        for raw in read("slots",[]) or []:
            category=int(raw.get("category",0) if isinstance(raw,dict) else getattr(raw,"category",0))
            count=max(0,int(raw.get("count",0) if isinstance(raw,dict) else getattr(raw,"count",0)))
            if category == BEAM:
                beam += count
            elif category == TORPEDO:
                torpedo += count
    return beam,torpedo


def _observed_counter_weapon_category(state) -> int|None:
    """Pick the family that actually fights the fleets we have seen.

    A beam destroyer swarm with a jammer, which is what the computer fielded,
    shrugs off a torpedo answer: jammers cut accuracy and each capital missile
    kills one cheap hull. Beams fire first and ignore jamming, so they thin
    that swarm. The same beam line, plus one jammer, is also the answer to a
    missile stack. Torpedoes stay on starbases, where range matters.
    """
    beam,torpedo=_observed_weapon_pressure(state)
    if beam <= 0 and torpedo <= 0:
        return None
    return BEAM


def _preferred_combat_weapon_category(state) -> int|None:
    """Choose a pure battle-line family from intel and active stock weapons."""
    counter=_observed_counter_weapon_category(state)
    available=proven_available_components(state)
    beams=[
        spec for spec in available.values()
        if int(spec.category) == BEAM and is_line_weapon(spec.category,spec.item_id)
    ]
    torpedoes=[
        spec for spec in available.values()
        if int(spec.category) == TORPEDO and is_line_weapon(spec.category,spec.item_id)
    ]
    if counter == BEAM and beams:
        return BEAM
    if counter == TORPEDO and torpedoes:
        return TORPEDO
    if not beams:
        return TORPEDO if torpedoes else None
    if not torpedoes:
        return BEAM

    # Before a real beam package, a Beta/Delta destroyer is the useful early
    # hull. Once beams exist they stay the battle line, including after Jihad.
    # Capital missiles on a ship dump one shot into each jammed beam destroyer.
    # Those missiles belong on a starbase, mixed with a beam that can clear chaff.
    if int(state.tech.weapons or 0) < 8:
        return TORPEDO
    return BEAM


def _raw_slots(record: dict) -> list[dict]:
    return list((record or {}).get("slots", []) or [])


def _slot_component_count(record: dict, category: int, item_id: int | None = None) -> int:
    return sum(
        max(0, int(raw.get("count", 0) or 0))
        for raw in _raw_slots(record)
        if int(raw.get("category", 0) or 0) == int(category)
        and (item_id is None or int(raw.get("item_id", 0) or 0) == int(item_id))
    )


def _role_design_records(state, role: str) -> list[dict]:
    """Return current design records for a strategic architecture family.

    Native snapshots sometimes arrive before their derived design-profile role
    has been refreshed.  Component signatures keep the family caps safe during
    that one-turn interval, rather than trusting a name such as ``Colonizer
    AI`` or a stale profile alone.
    """
    role = str(role).casefold()
    raws = {
        int(record.get("design_number", -1)): dict(record)
        for record in _design_dicts(state)
    }
    # A few synthetic/read-back-incomplete states carry a role-only profile
    # without the native design number.  It is useful descriptive data, but it
    # must not become a phantom second architecture and block a safe upgrade.
    profiles = {
        int(record["design_number"]): dict(record)
        for record in ((state.native or {}).get("design_profiles", []) or [])
        if not bool(record.get("is_starbase", False))
        and record.get("design_number") is not None
        and int(record.get("design_number", -1)) >= 0
    }
    slots = sorted(set(raws) | set(profiles))
    defining_category = {
        "colony": MECHANICAL,
        "miner": MINING_ROBOT,
        "minelayer": MINE_LAYER,
    }.get(role)
    result: list[dict] = []
    for slot in slots:
        raw = raws.get(slot, {})
        profile = profiles.get(slot, {})
        merged = {**raw, **profile, "design_number": slot}
        profile_role = str(profile.get("role", "")).casefold()
        component_match = False
        if role == "colony":
            component_match = any(
                is_colonizer_module(
                    int(slot.get("category", 0) or 0),
                    int(slot.get("item_id", 0) or 0),
                )
                and int(slot.get("count", 0) or 0) > 0
                for slot in _raw_slots(raw)
            )
        elif role == "combat":
            component_match = any(
                int(slot.get("category", 0) or 0) in (BEAM, TORPEDO)
                and int(slot.get("count", 0) or 0) > 0
                for slot in _raw_slots(raw)
            )
        elif defining_category is not None:
            component_match = _slot_component_count(raw, defining_category) > 0
        if profile_role == role or component_match:
            result.append(merged)
    return result


def colony_design_records(state) -> list[dict]:
    """Public policy view of every extant colony architecture."""
    return _role_design_records(state, "colony")


def _record_engine_id(record: dict) -> int | None:
    value = record.get("engine_id")
    if value is not None:
        return int(value)
    for raw in _raw_slots(record):
        if int(raw.get("category", 0) or 0) == ENGINE and int(raw.get("count", 0) or 0) > 0:
            return int(raw.get("item_id", 0) or 0)
    return None


def _record_cargo_capacity(record: dict) -> int:
    declared = record.get("cargo_capacity")
    if declared is not None:
        return max(0, int(declared or 0))
    hull_id = record.get("hull_id")
    hull = stock_native_hulls().get(int(hull_id)) if hull_id is not None else None
    return int(hull.cargo) if hull is not None else 0


def _colony_upgrade_reason(
    existing: list[dict], hull: HullNativeSpec, engine_id: int,
) -> str | None:
    """Explain a real improvement that justifies another colony design."""
    if not existing:
        return "initial dedicated colony architecture"
    if len(existing) >= MAX_COLONY_DESIGN_TYPES:
        return None
    current = max(
        existing,
        key=lambda record: (
            _record_cargo_capacity(record),
            int(record.get("fuel_capacity", 0) or 0),
            int(record.get("turn_designed", 0) or 0),
        ),
    )
    current_cargo = _record_cargo_capacity(current)
    current_engine = _record_engine_id(current)
    if current_cargo <= 0:
        return "replacement for a legacy non-cargo colony design"
    if (
        int(hull.hull_id) == COLONY_SHIP_HULL_ID
        and not any(int(record.get("hull_id", -1) or -1) == COLONY_SHIP_HULL_ID for record in existing)
    ):
        return (
            "replace general-purpose module freighters with the dedicated Colony Ship; "
            "reusable transports carry the follow-up settlement population"
        )
    if int(hull.cargo) > current_cargo:
        return f"cargo capacity improves {current_cargo}kT -> {int(hull.cargo)}kT"
    if current_engine is not None and int(engine_id) != int(current_engine):
        old_name = ENGINE_DATA.get(int(current_engine), (f"engine {current_engine}",))[0]
        new_name = ENGINE_DATA.get(int(engine_id), (f"engine {engine_id}",))[0]
        return f"engine architecture improves {old_name} -> {new_name}"
    return None


def _non_engine_slot_utilization(slots: tuple[ComponentRef, ...]) -> float:
    if len(slots) <= 1:
        return 1.0
    filled = sum(1 for component in slots[1:] if int(component.count) > 0)
    return filled / max(1, len(slots) - 1)


def _weapon_requirement(slots) -> int:
    """Highest Weapons-tech prerequisite represented by fitted armament."""
    tier = 0
    for raw in slots or ():
        category = int(raw.get("category", 0) if isinstance(raw, dict) else raw.category)
        item_id = int(raw.get("item_id", 0) if isinstance(raw, dict) else raw.item_id)
        count = int(raw.get("count", 0) if isinstance(raw, dict) else raw.count)
        if count <= 0 or category not in (BEAM, TORPEDO):
            continue
        tier = max(tier, int(component_spec(category, item_id).tech_required[1]))
    return tier


def _best_existing_combat_weapon_requirement(state) -> int:
    return max(
        (_weapon_requirement(_raw_slots(record)) for record in _role_design_records(state, "combat")),
        default=0,
    )


def _best_engine_for_role(state,role:str)->int|None:
    proven=proven_available_components(state)
    # IFE's Fuel Mizer is a deliberate early strategic choice, not merely a
    # component the optimizer happens to know.  In particular this keeps the
    # Colonizer Mk II proposal from silently recreating its existing DLL7
    # architecture instead of applying the requested IFE range upgrade.
    preferred=_engine_choice(state,role)
    if preferred is not None and (ENGINE,int(preferred)) in proven:
        return int(preferred)
    ids=[item for (cat,item) in proven if cat==ENGINE and item in ENGINE_DATA]
    if not ids: return _engine_choice(state,role)
    # Strategic design creation values usable W7 efficiency first, then mass.
    return min(ids,key=lambda eid:(int(ENGINE_DATA[eid][2][7]),int(ENGINE_DATA[eid][1]),-eid))


def synthesize_role_design(state,role:str,*,desired_hull_id:int|None=None,name:str|None=None,priority:int=105,engine_id:int|None=None)->NativeShipDesignPlan|None:
    """Build a legal design for a strategic role using race-legal components.

    This is the v8.8 general ship-builder.  It deliberately does not infer that
    an unseen component is researched.  It instead queries the current M-file
    tech levels plus the official PRT/LRT gates before fitting any component.
    """
    requested_role=str(role).lower()
    role="combat" if requested_role in _COMBAT_ROLE_ALIASES else requested_role
    combat_weapon_category=(
        _COMBAT_ROLE_ALIASES.get(requested_role)
        if role == "combat" else None
    )
    if role == "combat" and combat_weapon_category is None:
        combat_weapon_category=_preferred_combat_weapon_category(state)
    if role == "combat" and combat_weapon_category is None:
        return None
    if role == "minelayer" and len(_role_design_records(state, "minelayer")) >= MAX_MINELAYER_DESIGN_TYPES:
        return None
    engine_id=_best_engine_for_role(state,role) if engine_id is None else int(engine_id)
    if engine_id is None: return None
    if (ENGINE,int(engine_id)) not in proven_available_components(state):
        return None
    pool=_component_pool_for_role(
        state,role,combat_weapon_category=combat_weapon_category,
    )
    _enemy_beam_pressure,enemy_torpedo_pressure=_observed_weapon_pressure(state)
    defining=ROLE_DEFINING_CATEGORIES.get(role,())
    for hull in ranked_legal_ship_hulls(
        state,role,desired_hull_id=desired_hull_id,
    ):
        canonical=canonical_stock_hulls()[hull.hull_id]
        slots=[ComponentRef(0,0,0) for _ in range(hull.slot_count)]
        slots[0]=ComponentRef(ENGINE,int(engine_id),int(hull.engine_count))
        dedicated_computer_capacity=sum(
            int(slot.capacity) for slot in canonical.slots[1:]
            if int(slot.allowed_categories) == ELECTRICAL
        )
        for ss in canonical.slots[1:]:
            legal=[spec for spec in pool if ss.allows(spec.category)]
            if (
                role == "combat"
                and int(combat_weapon_category or 0) == TORPEDO
                and dedicated_computer_capacity > 0
                and int(ss.allowed_categories) != ELECTRICAL
            ):
                # Capital missile hulls already provide dedicated computer
                # banks. Preserve their flexible support positions for
                # shields/mobility rather than wasting a seventh computer.
                legal=[
                    spec for spec in legal
                    if not (
                        int(spec.category) == ELECTRICAL
                        and int(spec.item_id) in BATTLE_COMPUTERS
                    )
                ]
            if role == "combat" and int(combat_weapon_category or 0) == BEAM:
                # Keep the basic one-capacitor beam package economical and
                # leave the next support position to shields/computers.
                has_capacitor=any(
                    int(fitted.category) == ELECTRICAL
                    and int(fitted.item_id) in ENERGY_CAPACITORS
                    for fitted in slots
                )
                if has_capacitor:
                    legal=[
                        spec for spec in legal
                        if not (
                            int(spec.category) == ELECTRICAL
                            and int(spec.item_id) in ENERGY_CAPACITORS
                        )
                    ]
            if role == "combat" and enemy_torpedo_pressure > 0:
                has_jammer=any(
                    int(fitted.category) == ELECTRICAL
                    and int(fitted.item_id) not in BATTLE_COMPUTERS
                    and int(fitted.item_id) not in ENERGY_CAPACITORS
                    for fitted in slots
                )
                if has_jammer:
                    legal=[
                        spec for spec in legal
                        if not (
                            int(spec.category) == ELECTRICAL
                            and str(spec.name).startswith("Jammer ")
                        )
                    ]
            if not legal:
                continue
            # Category mission value dominates. Equal beam/torpedo combat
            # options use the battle-board rating, not an accidental ordering
            # of native item IDs. Item ID remains only a deterministic final
            # tie-breaker for genuinely equal fits.
            best=max(
                legal,
                key=lambda spec: _role_component_choice_key(
                    role,canonical,engine_id,ss,spec,
                    combat_weapon_category=combat_weapon_category,
                    enemy_torpedo_pressure=enemy_torpedo_pressure,
                ),
            )
            slots[ss.index]=ComponentRef(
                best.category,best.item_id,max(1,int(ss.capacity)),
            )
        if role == "combat":
            slots = _refit_combat_support_components(
                canonical,slots,pool,
                combat_weapon_category=combat_weapon_category,
                enemy_torpedo_pressure=enemy_torpedo_pressure,
            )
        slots_t=tuple(slots)
        if not _validate_candidate(state,hull,slots_t):
            continue
        # Mission-specific designs must actually contain their defining
        # equipment.  A candidate that cannot fit an observed, researched
        # scanner/bomb/weapon is skipped in favour of the next legal hull.
        if defining and not any(
            x.count>0 and x.category in defining for x in slots_t
        ):
            continue
        # A costly specialist must be a complete usable hull.  Scouts are the
        # deliberate exception: their small opening hull may have no current
        # legal fitting beyond engine + scanner.  Combat, bombing, and remote
        # mining designs must fill at least most of their available equipment
        # slots.  A mine layer is the other exception: its scanner and armor
        # slots stay empty so the frigate remains a cheap dispenser ship.
        utilization = _non_engine_slot_utilization(slots_t)
        # An early mine layer is a frigate with dispensers and an engine.
        # Shields and scanners spend minerals the field itself needs, and the
        # scanner and armor slots are supposed to stay empty.
        if role == "minelayer":
            if not any(int(x.category) == MINE_LAYER and int(x.count) > 0 for x in slots_t):
                continue
        elif role != "scout" and utilization < MIN_NON_ENGINE_SLOT_UTILIZATION:
            continue
        if role == "combat" and desired_hull_id is None:
            existing_weapon_tier = _best_existing_combat_weapon_requirement(state)
            candidate_weapon_tier = _weapon_requirement(slots_t)
            if existing_weapon_tier:
                # A new shield/armor layout with the same guns, engines, and
                # hull economics is not a strategic upgrade.  Wait for the
                # next race-legal armament capability, or keep the working
                # combat family intact.  The research planner derives that
                # capability from the stock component requirements; no fixed
                # Weapons-level milestone is assumed here.
                if candidate_weapon_tier <= existing_weapon_tier:
                    continue
        # A role is an architecture, not a new slot every turn.  In
        # particular the same armed hull serves both escort and combat duty;
        # exact architecture reuse leaves scarce slots for distinct designs.
        if _architecture_already_exists(state,hull.hull_id,slots_t):
            continue
        target=safe_recyclable_ship_slot(state,preferred_role=role)
        if target is None:
            return None
        slot,replace=target
        design_name=name or {
            "scout":"Long Range Scout AI",
            "combat":"Fleet Combat AI",
            "bomber":"Strike Bomber AI",
            "miner":"Remote Miner AI",
            "minelayer":"Mine Layer AI",
        }.get(role,f"{role.title()} AI")
        design=EncodedShipDesign(
            slot=slot,hull_id=hull.hull_id,pic=hull.pic,armor=hull.armor,
            turn_designed=max(0,int(state.year)-2400),slots=slots_t,
            name=design_name,staging_name=_staging_shipclass_name(state,hull.name),
            replace_existing=replace,
        )
        return NativeShipDesignPlan(design,role,priority,(
            f"Canonical ship builder selected {hull.name} from every current "
            f"research- and trait-legal stock hull for {role}; every fitted "
            "component is available from the current tech levels and PRT/LRT "
            f"gates, and every slot is checked against the StarsAPI-compatible "
            f"hull mask. {'Pure beam' if combat_weapon_category == BEAM else 'Pure torpedo' if combat_weapon_category == TORPEDO else 'Non-combat'} doctrine; "
            f"non-engine slot utilization={utilization:.0%}. Slot {slot} is "
            f"{'dead/recyclable' if replace else 'free'}."
        ))
    return None



def synthesize_colony_upgrade(
    state, *, desired_hull_id:int|None=None, name:str="Colonizer AI",
    priority:int=122, engine_id:int|None=None, require_demand:bool=True,
)->NativeShipDesignPlan|None:
    """Create an improved colonizer from every current legal hull option."""
    designs=_design_dicts(state)
    # A colonizer blueprint is demand-driven.  The all-legal hull selector
    # must not consume a design operation merely because an empty design slot
    # exists in a snapshot with no known settlement opportunity.
    if require_demand and not any(
        colony_planet_is_eligible(state,planet)
        for planet in state.planets
    ):
        return None
    # Standard races fit the Colonization Module.  Alternate Reality fits the
    # Orbital Construction Module, and only on the Colony Ship hull.
    available=proven_available_components(state)
    colonizer_item=(
        ORBITAL_CONSTRUCTION_MODULE_ITEM_ID
        if _prt_id(state) == PRT_ALTERNATE_REALITY
        else COLONIZATION_MODULE_ITEM_ID
    )
    if (MECHANICAL, colonizer_item) not in available: return None
    engine_id=_best_engine_for_role(state,"colony") if engine_id is None else int(engine_id)
    if engine_id is None: return None
    if (ENGINE,int(engine_id)) not in available: return None
    existing = colony_design_records(state)
    # Colonization modules on a non-cargo hull cannot transport colonists and
    # are therefore not a colony architecture at all.  The cargo requirement
    # is deliberately semantic (positive capacity), rather than a brittle
    # size cutoff.
    if len(existing) >= MAX_COLONY_DESIGN_TYPES:
        return None
    for hull in ranked_legal_ship_hulls(
        state,"colony",desired_hull_id=desired_hull_id,minimum_cargo_kt=1,
    ):
        upgrade_reason = _colony_upgrade_reason(existing, hull, engine_id)
        if upgrade_reason is None:
            continue
        slots=[ComponentRef(0,0,0) for _ in range(hull.slot_count)]
        slots[0]=ComponentRef(ENGINE,engine_id,hull.engine_count)
        canonical=canonical_stock_hulls()[hull.hull_id]
        target_slot_idx=next(
            (ss.index for ss in canonical.slots[1:] if ss.allows(MECHANICAL)),
            None,
        )
        if target_slot_idx is None:
            continue
        slots[target_slot_idx]=ComponentRef(MECHANICAL,colonizer_item,1)
        # A colony ship is retained for many turns.  Use every legal support
        # fitting that is already researched instead of leaving a costly hull
        # as an engine-plus-module shell.  A colonizer module is singular;
        # other mechanical slots prefer fuel tanks, then ordinary protection.
        support_weights={MECHANICAL:5.0,SHIELD:4.0,ARMOR:3.0,ELECTRICAL:2.0}
        support_pool=[
            spec for spec in available.values()
            if spec.category in support_weights
            and not is_colonizer_module(spec.category, spec.item_id)
        ]
        for ss in canonical.slots[1:]:
            if slots[ss.index].count > 0:
                continue
            legal=[spec for spec in support_pool if ss.allows(spec.category)]
            if not legal:
                continue
            best=max(legal,key=lambda spec:(
                support_weights.get(spec.category, 0.0), spec.item_id, -spec.mass,
            ))
            slots[ss.index]=ComponentRef(best.category,best.item_id,max(1,int(ss.capacity)))
        slots_t=tuple(slots)
        if not _validate_candidate(state,hull,slots_t):
            continue
        if _architecture_already_exists(state,hull.hull_id,slots_t):
            continue
        target=safe_recyclable_ship_slot(state,preferred_role="colony")
        if target is None:
            return None
        slot,replace=target
        design=EncodedShipDesign(
            slot=slot,hull_id=hull.hull_id,pic=hull.pic,armor=hull.armor,
            turn_designed=max(0,int(state.year)-2400),slots=slots_t,name=name,
            staging_name=_staging_shipclass_name(state,hull.name),replace_existing=replace,
        )
        return NativeShipDesignPlan(design,"colony",priority,(
            f"Colonizer synthesis selected {hull.name}; the dedicated Colony Ship is preferred over "
            f"general-purpose cargo hulls so reusable freighters deliver follow-up settlers. It fits the race-legal "
            f"{'Orbital Construction Module' if colonizer_item == ORBITAL_CONSTRUCTION_MODULE_ITEM_ID else 'Colonization Module'} with "
            f"engine item {engine_id}; research and PRT/LRT component gates passed, "
            f"slot geometry is canonical. Architecture justification: {upgrade_reason}. "
            "Production waits for next-M design read-back."
        ))
    return None


def _engine_id_for_requested_name(state, requested:str|None)->int|None:
    """Resolve a generic proposal's engine only when the race may fit it."""
    if requested is None:
        return _best_engine_for_role(state,"generic")
    wanted="".join(ch for ch in str(requested).casefold() if ch.isalnum())
    for (category,item),spec in proven_available_components(state).items():
        if category!=ENGINE:
            continue
        candidate="".join(ch for ch in spec.name.casefold() if ch.isalnum())
        if candidate==wanted:
            return int(item)
    return None


def _architecture_already_exists(state, hull_id:int, slots:tuple[ComponentRef,...])->bool:
    expected=[(int(s.category),int(s.item_id),int(s.count)) for s in slots]
    for design in _design_dicts(state):
        if int(design.get("hull_id",-1))!=int(hull_id):
            continue
        actual=[(
            int(raw.get("category",0)),int(raw.get("item_id",0)),int(raw.get("count",0))
        ) for raw in (design.get("slots",[]) or [])]
        if actual==expected:
            return True
    return False


def _starbase_architecture_already_exists(
    state, hull_id: int, slots: tuple[ComponentRef, ...]
) -> bool:
    """Avoid spending one of ten base slots on an existing exact architecture."""
    expected = [(int(s.category), int(s.item_id), int(s.count)) for s in slots]
    records = [
        record for record in ((state.native or {}).get("designs", []) or [])
        if bool(record.get("is_starbase"))
    ]
    # Some old/synthetic snapshots expose only the profile list.  It is still
    # enough to recognize a duplicate when those profiles preserve slots.
    records.extend((state.native or {}).get("starbase_profiles", []) or [])
    for design in records:
        if int(design.get("hull_id", -1) or -1) != int(hull_id):
            continue
        actual = [
            (int(raw.get("category", 0)), int(raw.get("item_id", 0)), int(raw.get("count", 0)))
            for raw in (design.get("slots", []) or [])
        ]
        if actual and actual == expected:
            return True
    return False


STARBASE_SUPPORT_CATEGORY_WEIGHTS = {
    # The hull supplies the actual shipyard/refuel capability.  These fittings
    # give a new hub local awareness and a modest defensive screen without
    # turning the first expansion base into an unaffordable late-game fortress.
    ORBITAL: 9.0,
    SHIELD: 7.0,
    ARMOR: 6.0,
    BEAM: 5.0,
    TORPEDO: 5.0,
    ELECTRICAL: 3.0,
}

STARBASE_DEFENSE_CATEGORY_WEIGHTS = {
    # A defensive base is a long-lived, stationary combat asset.  It should
    # use the weapon and protection capacity already paid for by its hull,
    # rather than resemble a lightly fitted expansion dock.
    BEAM: 20.0,
    TORPEDO: 20.0,
    SHIELD: 12.0,
    ARMOR: 11.0,
    ORBITAL: 8.0,
    ELECTRICAL: 5.0,
}


def _starbase_component_offensive_rating(component) -> float:
    """Evaluate one prospective weapon with the starbase battle modifiers.

    A synthetic Orbital Fort is enough here: its stock initiative and the
    universal starbase +1 weapon range are both applied, while hull armor is
    deliberately irrelevant to a component choice.  Non-weapons return zero
    so the role-category policy remains responsible for support equipment.
    """
    if int(component.category) not in (BEAM, TORPEDO):
        return 0.0
    return calculate_offensive_rating({
        "hull_id": 32,
        "slots": [{
            "category": int(component.category),
            "item_id": int(component.item_id),
            "count": 1,
        }],
    }).rating


def _planned_orbital_component(state, payload, available):
    """A gate or mass driver is fitted only when a plan actually asks for one."""
    requested=str(
        (payload or {}).get("orbital_plan")
        or ((getattr(state,"native",{}) or {}).get("planned_starbase_orbital"))
        or ""
    ).casefold()
    if requested in {"stargate","gate"}:
        pool=[spec for spec in available if int(spec.category)==ORBITAL and "stargate" in spec.name.casefold()]
    elif requested in {"mass_driver","mass driver","driver"}:
        pool=[spec for spec in available if int(spec.category)==ORBITAL and "driver" in spec.name.casefold()]
    else:
        return None
    return max(pool,key=lambda spec:int(spec.item_id),default=None)


def _best_starbase_weapon(options, capacity:int):
    return max(options,key=lambda spec:(
        calculate_offensive_rating({
            "hull_id":32,
            "slots":[{
                "category":int(spec.category),
                "item_id":int(spec.item_id),
                "count":max(1,int(capacity)),
            }],
        }).rating,
        int(spec.item_id),
    ))


def _count_steps(capacity: int, steps: tuple[int, ...]) -> list[int]:
    values: list[int] = []
    for step in steps:
        count = min(int(capacity), int(step))
        if count > 0 and count not in values:
            values.append(count)
    if int(capacity) not in values:
        values.append(int(capacity))
    return values


def _existing_weapon_count(state, hull_id: int, weapon) -> int:
    best = 0
    profiles = list((getattr(state, "native", {}) or {}).get("starbase_profiles", []) or [])
    profiles += list((getattr(state, "native", {}) or {}).get("designs", []) or [])
    for profile in profiles:
        if int(profile.get("hull_id", -1) or -1) != int(hull_id):
            continue
        for slot in profile.get("slots") or []:
            if int(slot.get("category", 0) or 0) != int(weapon.category):
                continue
            if int(slot.get("item_id", -1) or -1) != int(weapon.item_id):
                continue
            best = max(best, int(slot.get("count", 0) or 0))
    return best


def _phase_bill_years(state, canonical, weapon, shield, weapon_count: int, shield_count: int) -> int:
    """Years for the fullest world to pay this partial station bill."""
    from .planet_economy import decode_race_economy, estimated_mineral_output, estimated_operating_resources
    from .starbase_planner import _stock_starbase_hull_costs

    weapon_slots = [
        slot for slot in canonical.slots
        if slot.allows(weapon.category)
    ]
    shield_slots = [slot for slot in canonical.slots if slot.allows(SHIELD)]
    hull = _stock_starbase_hull_costs().get(int(canonical.hull_id), {})
    resources = int(hull.get("resources", 0) or 0)
    minerals = {
        "ironium": int(hull.get("ironium", 0) or 0),
        "boranium": int(hull.get("boranium", 0) or 0),
        "germanium": int(hull.get("germanium", 0) or 0),
    }
    resources += len(weapon_slots) * weapon_count * int(weapon.resource_cost)
    resources += len(shield_slots) * shield_count * int(getattr(shield, "resource_cost", 0) or 0)
    for name in ("ironium", "boranium", "germanium"):
        minerals[name] += len(weapon_slots) * weapon_count * int(getattr(weapon, name, 0) or 0)
        if shield is not None:
            minerals[name] += len(shield_slots) * shield_count * int(getattr(shield, name, 0) or 0)
    economy = decode_race_economy(getattr(state, "race", None))
    energy = int(getattr(getattr(state, "tech", None), "energy", 0) or 0)
    owned = [
        planet for planet in getattr(state, "planets", []) or []
        if getattr(planet, "owner", None) == getattr(state, "player_id", None)
    ]
    if not owned:
        return 99
    best = 99
    for planet in owned:
        produced = int(estimated_operating_resources(planet, economy, energy_tech=energy).get("estimated_resources") or 0)
        output = estimated_mineral_output(planet, economy).get("estimated_mineral_output") or {}
        resource_years = 99 if produced <= 0 else max(1, int(math.ceil(resources / produced)))
        mineral_years = 1
        for mineral, need in minerals.items():
            if need <= 0:
                continue
            rate = int(output.get(mineral) or 0)
            if rate <= 0:
                mineral_years = 99
                break
            mineral_years = max(mineral_years, int(math.ceil(need / rate)))
        best = min(best, max(resource_years, mineral_years))
    return best


def _phased_defense_counts(state, canonical, weapon, shield) -> tuple[int, int]:
    """First fit is thin. The next thicker fit waits until it is about two years away.

    Four weapons and eight shields are a legal first station. Twelve weapons
    and a full shield bank are the following step, not the opening bill.
    """
    from .critical_assets import FORTRESS_PHASE_TURNS

    weapon_steps = _count_steps(max(step.capacity for step in canonical.slots if step.allows(weapon.category)), (4, 8, 12, 16))
    shield_capacity = max((step.capacity for step in canonical.slots if step.allows(SHIELD)), default=1)
    shield_steps = _count_steps(shield_capacity, (8, 16))
    existing = _existing_weapon_count(state, int(canonical.hull_id), weapon)
    current = max((index for index, count in enumerate(weapon_steps) if count <= existing), default=-1)
    target = 0 if current < 0 else current
    if current >= 0 and current + 1 < len(weapon_steps):
        nxt = current + 1
        shield_count = shield_steps[min(nxt, len(shield_steps) - 1)]
        years = _phase_bill_years(state, canonical, weapon, shield, weapon_steps[nxt], shield_count)
        if years <= FORTRESS_PHASE_TURNS:
            target = nxt
    shield_count = shield_steps[min(target, len(shield_steps) - 1)]
    return weapon_steps[target], shield_count


def _fit_starbase_slots(state, canonical, payload) -> list[ComponentRef]:
    """Fill a starbase.

    A defense station, the one built on a critical planet, puts the best
    torpedo or capital missile in every weapon slot that can hold one.
    A support station keeps two torpedo slots and may use a beam elsewhere.
    One shield layer, then armor. The first electrical slot is a jammer,
    then a battle computer.
    """
    available=list(proven_available_components(state).values())
    slots=[ComponentRef(0,0,0) for _ in range(canonical.slot_count)]

    def _line(spec, category:int, *, missile:bool|None=None) -> bool:
        if int(spec.category)!=int(category) or not is_line_weapon(spec.category,spec.item_id):
            return False
        is_missile="missile" in spec.name.casefold()
        return True if missile is None else is_missile==missile

    missiles=[spec for spec in available if _line(spec,TORPEDO,missile=True)]
    torpedoes=[spec for spec in available if _line(spec,TORPEDO,missile=False)]
    beams=[spec for spec in available if _line(spec,BEAM)]
    defense=str((payload or {}).get("purpose") or "").casefold()=="defense"
    purpose=str((payload or {}).get("purpose") or "").casefold()
    from .critical_assets import FORTRESS_WEAPONS, fortress_plan_active
    fortress_weapons = defense and fortress_plan_active(state)
    if fortress_weapons:
        def _fortress_class(spec) -> bool:
            return int(spec.tech_required[1] or 0) >= FORTRESS_WEAPONS

        missiles=[spec for spec in missiles if _fortress_class(spec)]
        torpedoes=[spec for spec in torpedoes if _fortress_class(spec)]
    phased_weapon = None
    phased_shield = None
    if fortress_weapons and (missiles or torpedoes):
        weapon_spec = _best_starbase_weapon(missiles or torpedoes, 1)
        shield_spec = max(
            (spec for spec in available if int(spec.category) == SHIELD),
            key=lambda spec: int(spec.item_id),
            default=None,
        )
        phased_weapon, phased_shield = _phased_defense_counts(state, canonical, weapon_spec, shield_spec)
    weapon_slots=[slot for slot in canonical.slots if slot.allows(BEAM) or slot.allows(TORPEDO)]
    if purpose=="refuel":
        # A couple of weapons on an Orbital Fort is the refuel stop. Filling
        # the rest of the hull with armor makes a bill the new colony cannot pay.
        placed=0
        for slot in weapon_slots:
            if placed>=2:
                break
            capacity=max(1,int(slot.capacity))
            spec=None
            if beams and slot.allows(BEAM):
                spec=_best_starbase_weapon(beams,1)
            elif torpedoes and slot.allows(TORPEDO):
                spec=_best_starbase_weapon(torpedoes,1)
            if spec is None:
                continue
            count=min(capacity, 2-placed)
            slots[int(slot.index)]=ComponentRef(int(spec.category),int(spec.item_id),max(1,count))
            placed+=count
        return slots
    torpedo_slots_used=0
    fitted_beam=False
    for slot in weapon_slots:
        capacity=max(1,int(slot.capacity))
        if defense and (missiles or torpedoes) and slot.allows(TORPEDO):
            spec=_best_starbase_weapon(missiles or torpedoes,capacity)
            torpedo_slots_used+=1
        elif fortress_weapons and not (missiles or torpedoes):
            continue
        elif defense and beams and slot.allows(BEAM):
            spec=_best_starbase_weapon(beams,capacity)
            fitted_beam=True
        elif defense:
            continue
        elif missiles and slot.allows(TORPEDO) and torpedo_slots_used<2:
            spec=_best_starbase_weapon(missiles,capacity)
            torpedo_slots_used+=1
        elif missiles and beams and slot.allows(BEAM):
            sappers=[spec for spec in beams if "sapper" in spec.name.casefold()]
            spec=_best_starbase_weapon(sappers or beams,capacity)
            fitted_beam=True
        elif missiles and slot.allows(TORPEDO):
            spec=_best_starbase_weapon(missiles,capacity)
        elif missiles:
            continue
        else:
            reserve=bool(torpedoes and slot.allows(TORPEDO) and torpedo_slots_used<2)
            beam_better=False
            if beams and torpedoes and slot.allows(BEAM) and slot.allows(TORPEDO) and not reserve:
                beam_better=(
                    _best_starbase_weapon(beams,capacity)
                    and calculate_offensive_rating({
                        "hull_id":32,
                        "slots":[{"category":BEAM,"item_id":int(_best_starbase_weapon(beams,1).item_id),"count":1}],
                    }).rating
                    > calculate_offensive_rating({
                        "hull_id":32,
                        "slots":[{"category":TORPEDO,"item_id":int(_best_starbase_weapon(torpedoes,1).item_id),"count":1}],
                    }).rating
                )
            if reserve or (torpedoes and slot.allows(TORPEDO) and not beam_better):
                spec=_best_starbase_weapon(torpedoes,capacity)
                torpedo_slots_used+=1
            elif beams and slot.allows(BEAM):
                spec=_best_starbase_weapon(beams,capacity)
                fitted_beam=True
            else:
                continue
        count = phased_weapon if phased_weapon is not None and int(spec.category) == TORPEDO else capacity
        slots[int(slot.index)]=ComponentRef(int(spec.category),int(spec.item_id),max(1, min(capacity, int(count))))

    computers=[spec for spec in available if int(spec.category)==ELECTRICAL and int(spec.item_id) in BATTLE_COMPUTERS]
    jammers=[spec for spec in available if int(spec.category)==ELECTRICAL and str(spec.name).startswith("Jammer ")]
    capacitors=[spec for spec in available if int(spec.category)==ELECTRICAL and int(spec.item_id) in ENERGY_CAPACITORS]
    best_computer=max(computers,key=lambda spec:(BATTLE_COMPUTERS[int(spec.item_id)][1],int(spec.item_id)),default=None)
    best_jammer=max(jammers,key=lambda spec:int(spec.item_id),default=None)
    planned=_planned_orbital_component(state,payload,available)
    orbital_used=False
    electrical_index=0
    for slot in canonical.slots:
        if int(slots[int(slot.index)].count)>0:
            continue
        if planned is not None and not orbital_used and slot.allows(ORBITAL):
            slots[int(slot.index)]=ComponentRef(int(planned.category),int(planned.item_id),1)
            orbital_used=True
            continue
        if not slot.allows(ELECTRICAL):
            continue
        if electrical_index%2==0 and best_jammer is not None:
            choice=best_jammer
        elif best_computer is not None:
            choice=best_computer
        elif best_jammer is not None:
            choice=best_jammer
        elif fitted_beam and capacitors:
            choice=max(capacitors,key=lambda spec:int(spec.item_id))
        else:
            choice=None
        electrical_index+=1
        if choice is None:
            continue
        slots[int(slot.index)]=ComponentRef(int(choice.category),int(choice.item_id),max(1,int(slot.capacity)))

    for slot in canonical.slots:
        if int(slots[int(slot.index)].count)>0:
            continue
        shields=[spec for spec in available if int(spec.category)==SHIELD and slot.allows(SHIELD)]
        armors=[spec for spec in available if int(spec.category)==ARMOR and slot.allows(ARMOR)]
        # One shield layer keeps capital missiles from double-damaging armor
        # for the whole battle. Further protection is armor, which is what
        # survives a beam destroyer swarm after that shield is gone.
        shield_already=any(int(fitted.category)==SHIELD and int(fitted.count)>0 for fitted in slots)
        if armors and (shield_already or not shields):
            pool=armors
        else:
            pool=shields or armors
        if not pool:
            scanners=[spec for spec in available if int(spec.category)==SCANNER and slot.allows(SCANNER)]
            pool=scanners
        if not pool:
            continue
        spec=max(pool,key=lambda item:int(item.item_id))
        count = int(slot.capacity)
        if phased_shield is not None and int(spec.category) == SHIELD:
            count = phased_shield
        slots[int(slot.index)]=ComponentRef(int(spec.category),int(spec.item_id),max(1, min(int(slot.capacity), count)))
    return slots


def _choose_starbase_component(state, slot, *, purpose: str = "support") -> ComponentRef | None:
    """Pick a researched fitting suitable for a support or defensive base.

    Component availability is already constrained by the official stock MOD
    research requirements and Stars! PRT/LRT gates.  One unit per slot is
    intentional for the first network design: it creates a usable base that
    can actually clear the material gate, while future threat/tech proposals
    may select a stronger custom architecture instead of silently overwriting
    it.
    """
    weights = (
        STARBASE_DEFENSE_CATEGORY_WEIGHTS
        if str(purpose).casefold() == "defense"
        else STARBASE_SUPPORT_CATEGORY_WEIGHTS
    )
    candidates = [
        component for component in proven_available_components(state).values()
        if slot.allows(component.category)
        and component.category in weights
    ]
    if not candidates:
        return None
    best = max(candidates, key=lambda component: (
        weights[component.category],
        _starbase_component_offensive_rating(component),
        int(component.item_id),
        -int(component.mass),
    ))
    count = max(1, int(slot.capacity)) if str(purpose).casefold() == "defense" else 1
    return ComponentRef(int(best.category), int(best.item_id), count)


def synthesize_starbase_design_proposal(
    state, payload: dict[str, Any]
) -> NativeShipDesignPlan | None:
    """Compile a generic starbase proposal into a legal free-slot Type-27 design.

    Starbases use their own ten-slot namespace and have no engine requirement.
    We retain the same canonical slot validation and researched-component gates
    as the ship compiler, then emit a StarsAPI body with ``isStarbase`` set.
    """
    hull_id = payload.get("desired_hull_id")
    if hull_id is None:
        return None
    hull = stock_native_starbases().get(int(hull_id))
    canonical = canonical_stock_hulls().get(int(hull_id))
    if hull is None or canonical is None or not canonical.is_starbase or not canonical_hull_unlocked(canonical, state):
        return None

    purpose = str(payload.get("purpose", "support")).casefold()
    if purpose not in {"support", "defense", "refuel"}:
        return None
    if purpose == "refuel" and int(hull.hull_id) != 32:
        return None
    slots = _fit_starbase_slots(state, canonical, payload)
    slots_t = tuple(slots)
    if not _validate_candidate(state, hull, slots_t):
        return None
    if _starbase_architecture_already_exists(state, hull.hull_id, slots_t):
        return None

    slot = None
    for candidate in range(10):
        safety = starbase_design_slot_safety(state, candidate)
        if not (
            safety.design_exists
            or safety.installed_starbase_count
            or safety.queued_starbase_count
        ):
            slot = candidate
            break
    if slot is None:
        return None
    name = str(payload.get("name") or f"{hull.name} Support Base")
    priority = int(payload.get("priority", 100) or 100)
    design = EncodedShipDesign(
        slot=int(slot), hull_id=hull.hull_id, pic=hull.pic, armor=hull.armor,
        turn_designed=max(0, int(state.year) - 2400), slots=slots_t, name=name,
        staging_name=_staging_shipclass_name(state, hull.name), is_starbase=True,
    )
    component_count = sum(1 for fitted in slots_t if fitted.count > 0)
    offensive_rating = calculate_offensive_rating({
        "hull_id": int(hull.hull_id),
        "slots": [asdict(fitted) for fitted in slots_t],
    })
    return NativeShipDesignPlan(
        design, "starbase", priority,
        (
            f"Custom {hull.name} {purpose}-base design uses {component_count}/{hull.slot_count} "
            f"filled slots. A defense station fills every weapon slot with the best torpedo or "
            f"capital missile. Orbital slots take a jammer or battle computer "
            f"unless a gate or mass driver is planned. "
            f"Battle-board offensive rating={offensive_rating.display_rating} "
            f"({offensive_rating.expected_damage_per_volley:.1f} expected damage/volley). "
            "The hull grants ship construction and refueling; "
            "each fitting passed current StarsAPI stock-MOD research, PRT/LRT, and canonical slot checks. "
            f"Starbase design slot {slot} is free and awaits next-M read-back before construction is queued."
        ),
        confidence="EXPERIMENTAL_STARBASE_TYPE27",
    )


def synthesize_generic_design_proposal(state,payload:dict[str,Any])->NativeShipDesignPlan|None:
    """Compile a generic ``create_design`` proposal into a safe Type-27 ship.

    Generic proposals retain their strategic intent in the order stream.  This
    compiler is the sole bridge to native creation: it accepts only a
    researched, PRT/LRT-legal ship or starbase design and emits the same exact
    StarsAPI ``DesignBlock`` structure used by the dedicated design planners.
    """
    if bool(payload.get("is_starbase",False)):
        return synthesize_starbase_design_proposal(state, payload)
    role=str(payload.get("role","")).casefold()
    hull_id=payload.get("desired_hull_id")
    if hull_id is None:
        return None
    hull=stock_native_hulls().get(int(hull_id))
    if hull is None or not _hull_unlocked(state,hull):
        return None
    engine_id=_engine_id_for_requested_name(state,payload.get("desired_engine"))
    if engine_id is None:
        return None
    name=str(payload.get("name") or f"{role.title()} AI")
    priority=int(payload.get("priority",100) or 100)

    if role=="colony":
        return synthesize_colony_upgrade(
            state,desired_hull_id=hull.hull_id,name=name,priority=priority,
            engine_id=engine_id,require_demand=False,
        )

    if role=="miner" and not remote_miner_package_ready(state):
        # A midget miner spent now is the wrong ship. Wait for the best hull
        # and mining robot this race can research.
        return None
    if role=="miner" and not _remote_mining_targets(state) and _prt_id(state)!=PRT_ALTERNATE_REALITY:
        # Remote miners are an expensive dedicated asset, never a generic
        # hull filler.  Require an observed planetary mining assignment.
        # Alternate Reality's homeworld stays rich, so that race may design
        # the miner for its own world.
        return None

    if role=="freighter":
        slots=_empty_slot_array(hull,engine_id)
        if hull.hull_id==PRIVATEER_HULL_ID and (MECHANICAL,FUEL_TANK_ITEM_ID) in proven_available_components(state):
            slots=_privateer_onion_slots(hull,engine_id)
        if not _validate_candidate(state,hull,slots) or _architecture_already_exists(state,hull.hull_id,slots):
            return None
        target=safe_recyclable_ship_slot(state,preferred_role="freighter")
        if target is None:
            return None
        slot,replace=target
        design=EncodedShipDesign(
            slot=slot,hull_id=hull.hull_id,pic=hull.pic,armor=hull.armor,
            turn_designed=max(0,int(state.year)-2400),slots=slots,name=name,
            staging_name=_staging_shipclass_name(state,hull.name),replace_existing=replace,
        )
        return NativeShipDesignPlan(design,role,priority,(
            f"Generic freighter proposal compiled as {hull.name}; its requested engine and every fitted component passed current research and race-trait availability checks."
        ))

    # Scout and combat proposals use the same race-aware component pool as the
    # regular tactical synthesizer.  Other free-form roles are deliberately not
    # guessed into a Type-27 body.
    if role not in {"scout","combat","bomber","miner","minelayer"}:
        return None
    return synthesize_role_design(
        state,role,desired_hull_id=hull.hull_id,name=name,priority=priority,
        engine_id=engine_id,
    )


def synthesize_combat_upgrade(state)->NativeShipDesignPlan|None:
    profiles=[d for d in ((state.native or {}).get("design_profiles",[]) or []) if d.get("role")=="combat"]
    hostile=any(f.owner not in (None,state.player_id) for f in getattr(state,"fleets",[]) or [])
    if not profiles and not hostile: return None
    if max(int(state.tech.weapons or 0),int(state.tech.energy or 0))<4: return None
    # The combat and escort jobs share one family.  Once it is fielded, revisit
    # that family only on a deliberate cadence; `synthesize_role_design` then
    # refuses an identical architecture.  A researched hull/component upgrade
    # is therefore native-executable, while cosmetic duplicates never consume
    # a design slot.
    newest=max((int(d.get("turn_designed",0) or 0) for d in profiles),default=0)
    current_turn=max(0,int(state.year)-2400)
    if profiles and current_turn-newest<5:
        return None
    proposal=synthesize_role_design(state,"combat",priority=190 if hostile else 168)
    if proposal is None: return None
    return proposal


def _remote_mining_targets(state) -> list:
    """Observed unowned mineral worlds worth a dedicated remote miner."""
    targets=[]
    for planet in state.planets:
        concentrations=(planet.native or {}).get("mineral_concentrations") or []
        if (
            planet.owner is None and planet.observed and len(concentrations) >= 3
            and all(value is not None for value in concentrations[:3])
            and sum(max(0,int(value)) for value in concentrations[:3]) >= 150
        ):
            targets.append(planet)
    return targets


def synthesize_remote_miner(state) -> NativeShipDesignPlan | None:
    """Create one legal miner design once the top hull and robot exist."""
    if not remote_miner_package_ready(state):
        return None
    targets=_remote_mining_targets(state)
    homeworld_mine=_prt_id(state)==PRT_ALTERNATE_REALITY and any(
        planet.owner==state.player_id for planet in state.planets
    )
    profiles=[d for d in ((state.native or {}).get("design_profiles",[]) or []) if d.get("role")=="miner"]
    if profiles or not (targets or homeworld_mine):
        return None
    hull,_robot=top_remote_miner_package(state)
    proposal=synthesize_role_design(
        state,"miner",desired_hull_id=None if hull is None else int(hull.hull_id),
        name="Remote Miner AI",priority=136,
    )
    if proposal is None:
        return None
    return NativeShipDesignPlan(
        proposal.encoded,proposal.role,proposal.priority,
        proposal.reason + (
            " It mines the Alternate Reality homeworld, whose mineral rate stays above the colonies."
            if homeworld_mine and not targets else
            f" It addresses {len(targets)} observed high-concentration unowned mineral world(s)."
            + (" The same hull can also mine the Alternate Reality homeworld." if homeworld_mine else "")
        ),
    )


def plan_native_ship_design(state, plan=None) -> NativeShipDesignPlan | None:
    """At most one experimental native utility design per turn."""
    candidates = [x for x in (
        synthesize_onion_privateer(state),
        synthesize_freighter_upgrade(state),
        synthesize_remote_miner(state),
        synthesize_medium_population_transport(state),
        synthesize_scout_upgrade(state),
        synthesize_colony_upgrade(state),
        synthesize_combat_upgrade(state),
    ) if x is not None]
    return max(candidates, key=lambda x: x.priority, default=None)
