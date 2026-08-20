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
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .design_legality import ComponentCategory, ComponentRef, HULL_RULES, validate_design
from .expansion_network import evaluate_expansion_network
from .fuel_planner import ENGINE_DATA, best_range_ly, design_fuel_profile
from .logistics_capacity import evaluate_logistics_capacity, POPULATION_PULSE_KT
from .native.design_change import EncodedShipDesign
from .standard_mod import TechLevels, parse_mod_file

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
    path = Path(__file__).with_name("data_hulls.mod")
    out = {}
    for parts in csv.reader(path.read_text(encoding="latin-1").splitlines()):
        if len(parts) < 4 or int(parts[0]) != 15:
            continue
        nums = [int(x) if x else 0 for x in parts[3:]]
        hid = int(nums[0])
        out[hid] = HullNativeSpec(
            hull_id=hid,
            name=str(parts[2]),
            requirements=tuple(int(x) for x in nums[1:7]),
            pic=int(nums[12]),
            armor=int(nums[15]),
            cargo=int(nums[13]),
            fuel=int(nums[14]),
            engine_count=int(HULL_RULES[hid].slots[0].max_count),
            slot_count=len(HULL_RULES[hid].slots),
        )
    return out


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
    have = tuple(getattr(_tech(state), f) for f in (
        "energy", "weapons", "propulsion", "construction", "electronics", "biotechnology"
    ))
    return all(int(have[i]) >= int(hull.requirements[i]) for i in range(6))


def _lrts(state) -> set[str]:
    return {str(x).upper() for x in ((state.race.native or {}).get("lrts", []) or [])}


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
    db = parse_mod_file(Path(__file__).with_name("data_hulls.mod"))
    # The bundled data_hulls.mod is authoritative for hull geometry but may not
    # contain component rows. Slot legality is still exact. Component
    # availability is separately constrained by this synthesizer: Fuel Mizer
    # requires IFE+P2; other engines are reused from an existing own design.
    available = db.available_components(_tech(state)) if db.components else None
    rules = db.hulls or HULL_RULES
    result = validate_design(hull.hull_id, list(slots), hull_rules=rules, available_components=available)
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
        name="Onion Privateer",staging_name=hull.name,replace_existing=replace,
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
        name="Population Shuttle",staging_name=hull.name,replace_existing=replace,
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
        name="Bulk Freighter AI",staging_name=hull.name,replace_existing=replace,
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
        name="Long Range Scout II", staging_name=hull.name, replace_existing=replace,
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


def plan_native_ship_design(state, plan=None) -> NativeShipDesignPlan | None:
    """At most one experimental native utility design per turn."""
    candidates = [x for x in (
        synthesize_onion_privateer(state),
        synthesize_freighter_upgrade(state),
        synthesize_medium_population_transport(state),
        synthesize_scout_upgrade(state),
    ) if x is not None]
    return max(candidates, key=lambda x: x.priority, default=None)
