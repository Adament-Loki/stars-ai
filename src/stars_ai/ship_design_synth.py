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

from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

from .design_legality import ComponentCategory, ComponentRef, HULL_RULES, validate_design
from .expansion_network import evaluate_expansion_network
from .fuel_planner import ENGINE_DATA, best_range_ly, design_fuel_profile
from .starsapi_items import (
    stock_hulls as canonical_stock_hulls, hull_unlocked as canonical_hull_unlocked,
    proven_available_components, ENGINE, SCANNER, SHIELD, ARMOR, BEAM, TORPEDO,
    BOMB, MINING_ROBOT, MINE_LAYER, ORBITAL, ELECTRICAL, MECHANICAL,
)
from .logistics_capacity import evaluate_logistics_capacity, POPULATION_PULSE_KT
from .native.design_change import EncodedShipDesign, starbase_design_slot_safety
from .standard_mod import TechLevels

UNIVERSAL_FREIGHTER_HULLS = (0, 1, 2)  # Small, Medium, Large. Super Freighter is IS-specific.
MEDIUM_FREIGHTER_HULL_ID = 1
LARGE_FREIGHTER_HULL_ID = 2
SCOUT_HULL_ID = 4
PRIVATEER_HULL_ID = 11
FUEL_MIZER_ITEM_ID = 2
FUEL_TANK_ITEM_ID = 5
FUEL_TANK_COUNT = 3


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
        hull.hull_id,list(slots),hull_rules=HULL_RULES,available_components=available
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
    if logistics.population_lane_count<=0 and not network.layer1_pending_ids:
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
            f"Slot {slot} is {'dead/recyclable' if replace else 'free'}; production waits for next-M read-back."
        ),
    )


def synthesize_medium_population_transport(state) -> NativeShipDesignPlan | None:
    """C3 fallback when no 200-kT compact population carrier exists yet."""
    logistics=evaluate_logistics_capacity(state)
    if logistics.desired_population_freighters<=0 or _compact_population_carrier_exists(state):
        return None
    hull=stock_native_hulls().get(MEDIUM_FREIGHTER_HULL_ID)
    if hull is None or not _hull_unlocked(state,hull):
        return None
    engine_id=_engine_choice(state,"freighter")
    if engine_id is None:
        return None
    target_slot=safe_recyclable_ship_slot(state,preferred_role="freighter")
    if target_slot is None:
        return None
    slot,replace=target_slot
    slots=_empty_slot_array(hull,engine_id)
    if not _validate_candidate(state,hull,slots):
        return None
    design=EncodedShipDesign(
        slot=slot,hull_id=hull.hull_id,pic=hull.pic,armor=hull.armor,
        turn_designed=max(0,int(state.year)-2400),slots=slots,
        name="Population Shuttle",staging_name=_staging_shipclass_name(state,hull.name),replace_existing=replace,
    )
    return NativeShipDesignPlan(
        design,"freighter",133,
        f"No verified compact 200-kT population carrier exists. Create a Medium Freighter shuttle for phased 20k-colonist onion pulses; slot {slot} is {'dead/recyclable' if replace else 'free'}. Production waits for read-back.",
    )


def synthesize_freighter_upgrade(state) -> NativeShipDesignPlan | None:
    """Large Freighter only when bulk industrial mineral logistics justify it."""
    logistics=evaluate_logistics_capacity(state)
    if not logistics.large_freighter_valuable:
        return None
    hull=stock_native_hulls().get(LARGE_FREIGHTER_HULL_ID)
    if hull is None or not _hull_unlocked(state,hull):
        return None
    current=_best_profile(state,"freighter")
    current_cargo=max(
        (int(d.get("cargo_capacity",0) or 0) for d in ((state.native or {}).get("design_profiles",[]) or []) if d.get("role")=="freighter"),
        default=0,
    )
    if current_cargo>=hull.cargo:
        return None
    engine_id=_engine_choice(state,"freighter")
    if engine_id is None:
        return None
    target_slot=safe_recyclable_ship_slot(state,preferred_role="freighter")
    if target_slot is None:
        return None
    slot,replace=target_slot
    slots=_empty_slot_array(hull,engine_id)
    if not _validate_candidate(state,hull,slots):
        return None
    design=EncodedShipDesign(
        slot=slot,hull_id=hull.hull_id,pic=hull.pic,armor=hull.armor,
        turn_designed=max(0,int(state.year)-2400),slots=slots,
        name="Bulk Freighter AI",staging_name=_staging_shipclass_name(state,hull.name),replace_existing=replace,
    )
    return NativeShipDesignPlan(
        design,"freighter",140,
        (
            f"Create Large Freighter for BULK industrial logistics, not routine population movement: "
            f"transferable mineral pressure={logistics.bulk_transferable_kt} kT, active shipyard builds="
            f"{logistics.active_shipyard_build_count}. Hull requires {hull.engine_count} identical engine(s); "
            f"base cargo={hull.cargo} kT. Slot {slot} is {'dead/recyclable' if replace else 'free'}. "
            "Production waits for next-M read-back."
        ),
    )


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
    "combat":{BEAM:9.0,TORPEDO:9.0,SHIELD:6.0,ARMOR:5.0,ELECTRICAL:4.0,MECHANICAL:1.0,SCANNER:.5},
    "bomber":{BOMB:12.0,SHIELD:4.0,ARMOR:3.0,ELECTRICAL:2.0,MECHANICAL:1.0},
    "miner":{MINING_ROBOT:12.0,SHIELD:3.0,ARMOR:2.0,ELECTRICAL:2.0,MECHANICAL:1.0},
    "minelayer":{MINE_LAYER:12.0,SHIELD:3.0,ARMOR:2.0,ELECTRICAL:2.0,MECHANICAL:1.0},
}

ROLE_HULL_PREFERENCE={
    "combat":(10,9,8,7,6,5),
    "bomber":(19,18,17,16),
    "miner":(24,23,22,21,20),
    # Space Demolition receives the dedicated hulls first. Every other race
    # can fit standard Mine Dispensers into a compatible general-purpose hull;
    # the Frigate is the economical first option and later hulls scale field
    # capacity as construction improves.
    "minelayer":(28,27,5,6,7,8,10,11,13,12,29),
}


def _component_pool_for_role(state,role:str):
    proven=proven_available_components(state)
    weights=ROLE_CATEGORY_WEIGHTS.get(role,{})
    return [spec for spec in proven.values() if spec.category in weights]


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
    role=str(role).lower()
    preferences=((desired_hull_id,) if desired_hull_id is not None else ROLE_HULL_PREFERENCE.get(role,()))
    hull=None
    for hid in preferences:
        candidate=stock_native_hulls().get(int(hid))
        if candidate is not None and _hull_unlocked(state,candidate):
            hull=candidate; break
    if hull is None: return None
    engine_id=_best_engine_for_role(state,role) if engine_id is None else int(engine_id)
    if engine_id is None: return None
    if (ENGINE,int(engine_id)) not in proven_available_components(state):
        return None
    canonical=canonical_stock_hulls()[hull.hull_id]
    slots=[ComponentRef(0,0,0) for _ in range(hull.slot_count)]
    slots[0]=ComponentRef(ENGINE,int(engine_id),int(hull.engine_count))
    pool=_component_pool_for_role(state,role)
    weights=ROLE_CATEGORY_WEIGHTS.get(role,{})
    for ss in canonical.slots[1:]:
        legal=[spec for spec in pool if ss.allows(spec.category)]
        if not legal: continue
        # Category mission value dominates. Within a category later observed
        # item IDs are a weak proxy for a later component; lower mass breaks ties.
        best=max(legal,key=lambda spec:(weights.get(spec.category,0.0),spec.item_id,-spec.mass))
        slots[ss.index]=ComponentRef(best.category,best.item_id,max(1,int(ss.capacity)))
    slots_t=tuple(slots)
    if not _validate_candidate(state,hull,slots_t): return None
    # Mission-specific designs must actually contain their defining equipment.
    defining={"combat":(BEAM,TORPEDO),"bomber":(BOMB,),"miner":(MINING_ROBOT,),"minelayer":(MINE_LAYER,)}.get(role,())
    if defining and not any(x.count>0 and x.category in defining for x in slots_t): return None
    # A role is an architecture, not a new slot every turn.  In particular the
    # same armed hull serves both escort and combat duty; exact architecture
    # reuse leaves scarce slots for genuinely distinct logistics/mining ships.
    if _architecture_already_exists(state,hull.hull_id,slots_t): return None
    target=safe_recyclable_ship_slot(state,preferred_role=role)
    if target is None: return None
    slot,replace=target
    design_name=name or {"combat":"Fleet Combat AI","bomber":"Strike Bomber AI","miner":"Remote Miner AI","minelayer":"Mine Layer AI"}.get(role,f"{role.title()} AI")
    design=EncodedShipDesign(slot=slot,hull_id=hull.hull_id,pic=hull.pic,armor=hull.armor,turn_designed=max(0,int(state.year)-2400),slots=slots_t,name=design_name,staging_name=_staging_shipclass_name(state,hull.name),replace_existing=replace)
    return NativeShipDesignPlan(design,role,priority,(
        f"Canonical ship builder selected {hull.name} for {role}; every fitted component is available from the current tech levels and PRT/LRT gates, and every slot is checked against the StarsAPI-compatible hull mask. Slot {slot} is {'dead/recyclable' if replace else 'free'}."
    ))



def synthesize_colony_upgrade(
    state, *, desired_hull_id:int|None=None, name:str="Colonizer AI",
    priority:int=122, engine_id:int|None=None,
)->NativeShipDesignPlan|None:
    """Create an improved colonizer from the current race-legal inventory."""
    designs=_design_dicts(state)
    profiles=[d for d in ((state.native or {}).get("design_profiles",[]) or []) if d.get("role")=="colony"]
    if not profiles: return None
    # Colonization Module is Mechanical item 0 in stock Stars!.
    available=proven_available_components(state)
    if (MECHANICAL,0) not in available: return None
    hull=None
    hull_ids=((int(desired_hull_id),) if desired_hull_id is not None else (15,14))
    for hid in hull_ids:
        candidate=stock_native_hulls().get(hid)
        if candidate is not None and _hull_unlocked(state,candidate): hull=candidate; break
    if hull is None: return None
    engine_id=_best_engine_for_role(state,"colony") if engine_id is None else int(engine_id)
    if engine_id is None: return None
    if (ENGINE,int(engine_id)) not in available: return None
    slots=[ComponentRef(0,0,0) for _ in range(hull.slot_count)]
    slots[0]=ComponentRef(ENGINE,engine_id,hull.engine_count)
    canonical=canonical_stock_hulls()[hull.hull_id]
    target_slot_idx=next((ss.index for ss in canonical.slots[1:] if ss.allows(MECHANICAL)),None)
    if target_slot_idx is None: return None
    slots[target_slot_idx]=ComponentRef(MECHANICAL,0,1)
    slots_t=tuple(slots)
    if not _validate_candidate(state,hull,slots_t): return None
    # Avoid duplicate architecture.
    for d in designs:
        if int(d.get("hull_id",-1))!=hull.hull_id: continue
        raw=d.get("slots",[]) or []
        if len(raw)!=len(slots_t): continue
        same=True
        for a,b in zip(raw,slots_t):
            if (int(a.get("category",0)),int(a.get("item_id",0)),int(a.get("count",0)))!=(b.category,b.item_id,b.count): same=False; break
        if same: return None
    target=safe_recyclable_ship_slot(state,preferred_role="colony")
    if target is None: return None
    slot,replace=target
    design=EncodedShipDesign(slot=slot,hull_id=hull.hull_id,pic=hull.pic,armor=hull.armor,turn_designed=max(0,int(state.year)-2400),slots=slots_t,name=name,staging_name=_staging_shipclass_name(state,hull.name),replace_existing=replace)
    return NativeShipDesignPlan(design,"colony",priority,(
        f"Colonizer synthesis fits the race-legal Colonization Module on {hull.name} with engine item {engine_id}; research and PRT/LRT component gates passed, slot geometry is canonical, and production waits for next-M design read-back."
    ))


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


def _choose_starbase_component(state, slot) -> ComponentRef | None:
    """Pick one economical, researched fitting legal for a base slot.

    Component availability is already constrained by the official stock MOD
    research requirements and Stars! PRT/LRT gates.  One unit per slot is
    intentional for the first network design: it creates a usable base that
    can actually clear the material gate, while future threat/tech proposals
    may select a stronger custom architecture instead of silently overwriting
    it.
    """
    candidates = [
        component for component in proven_available_components(state).values()
        if slot.allows(component.category)
        and component.category in STARBASE_SUPPORT_CATEGORY_WEIGHTS
    ]
    if not candidates:
        return None
    best = max(candidates, key=lambda component: (
        STARBASE_SUPPORT_CATEGORY_WEIGHTS[component.category],
        int(component.item_id),
        -int(component.mass),
    ))
    return ComponentRef(int(best.category), int(best.item_id), 1)


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

    slots = [ComponentRef(0, 0, 0) for _ in range(hull.slot_count)]
    for slot in canonical.slots:
        fitting = _choose_starbase_component(state, slot)
        if fitting is not None:
            slots[int(slot.index)] = fitting
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
    return NativeShipDesignPlan(
        design, "starbase", priority,
        (
            f"Custom {hull.name} support-base design uses {component_count}/{hull.slot_count} "
            "economical researched fittings. The hull grants ship construction and refueling; "
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
            engine_id=engine_id,
        )

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
    """Create one legal miner design when observed remote work exists."""
    targets=_remote_mining_targets(state)
    profiles=[d for d in ((state.native or {}).get("design_profiles",[]) or []) if d.get("role")=="miner"]
    if not targets or profiles:
        return None
    proposal=synthesize_role_design(state,"miner",name="Remote Miner AI",priority=136)
    if proposal is None:
        return None
    return NativeShipDesignPlan(
        proposal.encoded,proposal.role,proposal.priority,
        proposal.reason + f" It addresses {len(targets)} observed high-concentration unowned mineral world(s).",
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
