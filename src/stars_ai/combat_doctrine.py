
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable
import math

from .starsapi_items import BEAM, ELECTRICAL, ENGINE, MECHANICAL, TORPEDO, stock_hulls


class WeaponDoctrine(str, Enum):
    NONE = "none"
    BEAM = "beam"
    TORPEDO = "torpedo"
    MIXED = "mixed"


class ModernizationDecision(str, Enum):
    BUILD_CURRENT = "build_current"
    TECH_THEN_REBUILD = "tech_then_rebuild"
    HOLD_AND_TECH = "hold_and_tech"
    FIGHT_NOW = "fight_now"
    RETREAT_AND_PRESERVE = "retreat_and_preserve"


@dataclass(frozen=True)
class OffensiveRating:
    """Battle-board-derived offensive score for one ship or starbase design.

    The game UI's ``Rating`` formula was never published.  This is deliberately
    a separate AI metric: it uses the stock battle statistics and rules, while
    exposing every input and multiplier so a decision can be audited.
    """

    rating: float
    display_rating: int
    expected_damage_per_volley: float
    shield_only_damage_per_volley: float
    battle_movement: float
    average_firing_initiative: float
    maximum_firing_initiative: float
    average_weapon_range: float
    average_torpedo_accuracy: float
    is_starbase: bool
    weapon_count: int
    model: str = "battle-board-offense-v1"


# The Technical Reference's "Optimal Speed" is the manual's
# ``Ideal_Speed_of_Engine`` for the battle-movement formula.  IDs are native
# zero-based component IDs, as used by DesignBlock slots.
_ENGINE_IDEAL_BATTLE_SPEED = {
    0: 6, 1: 5, 2: 6, 3: 6, 4: 7, 5: 8, 6: 9, 7: 10,
    8: 6, 9: 10, 10: 6, 11: 7, 12: 8, 13: 9, 14: 10, 15: 10,
}

# Stock hull initiatives from the Technical Reference.  A zero fallback is
# intentional for utility hulls that have no published combat initiative.
_STOCK_HULL_INITIATIVE = {
    4: 1, 5: 4, 6: 3, 7: 5, 8: 5, 9: 10, 10: 10,
    11: 3, 12: 4, 13: 4, 30: 2, 31: 2,
    32: 10, 33: 12, 34: 14, 35: 16, 36: 18,
}

# (initiative bonus, torpedo-inaccuracy reduction).  Multiple computers stack
# as separate reductions to the remaining inaccuracy, exactly like the rule
# book's accuracy examples.
# Native zero-based Electrical component IDs. Multiple copies stack as
# separate reductions to the remaining torpedo inaccuracy, exactly like the
# rule book's accuracy examples. Kept public so the ship fitter can make the
# same component-synergy decision as the offensive-rating model.
BATTLE_COMPUTERS = {
    5: (1, 0.20),  # Battle Computer
    6: (2, 0.30),  # Battle Super Computer
    7: (3, 0.50),  # Battle Nexus
}

# Energy Capacitor's +10% beam damage is a component relationship, not a
# property of a single gun.  The native zero-based Electrical item ID comes
# from the active stock MOD and is deliberately shared with the fitter.
ENERGY_CAPACITORS = {12: 0.10}


@dataclass(frozen=True)
class _StockWeapon:
    name: str
    category: int
    damage: float
    weapon_range: float
    initiative: float
    accuracy: float = 1.0
    shield_only: bool = False
    can_hit_ships: bool = True


@dataclass
class ShipCombatProfile:
    label: str
    hull_id: int | None
    mass: float
    resource_cost: float
    beam_strength: float
    torpedo_strength: float
    shield_strength: float
    armor_strength: float
    accuracy: float
    initiative: float
    combat_speed: float
    range_profile: float
    tech_generation: float
    doctrine: WeaponDoctrine
    effective_combat_value: float
    combat_value_per_resource: float
    obsolete_score: float = 0.0
    offensive_rating: float = 0.0
    display_offensive_rating: int = 0
    expected_damage_per_volley: float = 0.0
    battle_movement: float = 0.0
    firing_initiative: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FleetCombatProfile:
    owner_id: int | None
    ship_count: int
    total_mass: float
    beam_strength: float
    torpedo_strength: float
    shield_strength: float
    armor_strength: float
    effective_combat_value: float
    modern_combat_value: float
    obsolete_combat_value: float
    average_tech_generation: float
    design_profiles: list[ShipCombatProfile] = field(default_factory=list)


@dataclass
class RelativeMilitaryAssessment:
    our_value: float
    enemy_value: float
    relative_strength: float
    our_modern_fraction: float
    enemy_modern_fraction: float
    our_avg_tech: float
    enemy_avg_tech: float
    tech_gap: float
    expected_trade_ratio: float


@dataclass
class ModernizationPlan:
    decision: ModernizationDecision
    reason: str
    current_design_efficiency: float
    prospective_design_efficiency: float
    expected_turns_to_upgrade: int
    territorial_risk: float
    sacrifice_budget: float
    research_priorities: list[str]
    production_guidance: str
    engagement_guidance: str


_MISSING = object()


def _value(value: Any, *names: str, default: Any = None) -> Any:
    """Read either native JSON/dict data or the small test/design objects."""
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        found = getattr(value, name, _MISSING)
        if found is not _MISSING:
            return found
    return default


def _cat(slot: Any) -> int:
    return int(_value(slot, "category", "category_mask", default=0) or 0)


def _count(slot: Any) -> int:
    return int(_value(slot, "count", default=0) or 0)


def _item(slot: Any) -> int:
    return int(_value(slot, "item_id", "itemId", default=0) or 0)


def _slot_strength(slot: Any, item_db: Any | None = None) -> tuple[float, float, float, float]:
    """
    Returns (beam, torpedo, shield, armor) contribution.

    If an item database is available and exposes concrete component stats, use
    them. Otherwise use conservative category-aware proxies so doctrine can
    reason about composition without pretending exact Stars! battle math.
    """
    cat = _cat(slot)
    n = max(1, _count(slot))
    item_id = _item(slot)

    stats = None
    if item_db is not None:
        # Support several likely item-db shapes.
        getter = getattr(item_db, "get", None)
        if callable(getter):
            try:
                stats = getter(cat, item_id)
            except TypeError:
                try:
                    stats = getter(item_id)
                except Exception:
                    stats = None
        if stats is None and isinstance(item_db, dict):
            stats = item_db.get((cat, item_id), item_db.get(item_id))

    def stat(name: str, default: float) -> float:
        if stats is None:
            return default
        if isinstance(stats, dict):
            v = stats.get(name)
        else:
            v = getattr(stats, name, None)
        return default if v is None else float(v)

    # StarsAPI category constants already used elsewhere in the project:
    # beam 16, torpedo 32, shield 4, armor 8.
    if cat == 16:
        return (n * stat("damage", 8.0 + item_id * 1.5), 0.0, 0.0, 0.0)
    if cat == 32:
        return (0.0, n * stat("damage", 12.0 + item_id * 2.0), 0.0, 0.0)
    if cat == 4:
        return (0.0, 0.0, n * stat("shield", 20.0 + item_id * 4.0), 0.0)
    if cat == 8:
        return (0.0, 0.0, 0.0, n * stat("armor", 30.0 + item_id * 5.0))
    return (0.0, 0.0, 0.0, 0.0)


@lru_cache(maxsize=1)
def _stock_weapon_stats() -> dict[tuple[int, int], _StockWeapon]:
    """Read actual stock weapon battle fields from the bundled UNEDITED.MOD."""
    path = Path(__file__).with_name("data_unedited.mod")
    result: dict[tuple[int, int], _StockWeapon] = {}
    for parts in csv.reader(path.read_text(encoding="latin-1").splitlines()):
        if len(parts) < 20 or parts[0] not in ("2", "3"):
            continue
        mod_category = int(parts[0])
        category = BEAM if mod_category == 2 else TORPEDO
        item_id = int(parts[1]) - 1
        nums = [int(value) if value else 0 for value in parts[3:]]
        # The layout is documented by StarsAPI Items: range, damage, weapon
        # initiative and (for torpedoes) hit chance begin at index 13.
        weapon_range = float(nums[13])
        damage = float(nums[14])
        initiative = float(nums[15])
        accuracy = float(nums[16]) / 100.0 if category == TORPEDO else 1.0
        special = int(nums[16]) if category == BEAM else 0
        name = parts[2]
        result[(category, item_id)] = _StockWeapon(
            name=name,
            category=category,
            damage=damage,
            weapon_range=weapon_range,
            initiative=initiative,
            accuracy=max(0.0, min(1.0, accuracy)),
            shield_only=(category == BEAM and special == 1),
            # Multi Contained Munition is a mine weapon, not a ship weapon.
            can_hit_ships=(name != "Multi Contained Munition"),
        )
    return result


def weapon_stats(category: int, item_id: int) -> _StockWeapon | None:
    """Return active-catalog battle statistics for a weapon component."""
    return _stock_weapon_stats().get((int(category), int(item_id)))


def is_line_weapon(category: int, item_id: int) -> bool:
    """Whether a weapon can attack ships as normal battle-line armament.

    Shield sappers and the Multi Contained Munition have important niche uses,
    but neither is a substitute for an anti-ship beam or missile line.
    """
    weapon = weapon_stats(category, item_id)
    # Zero-range weapons are point-blank niche tools. They can be useful in an
    # explicit assault/counter plan, but are not a dependable default line:
    # the battle board may never let them fire against a kiting opponent.
    return bool(
        weapon
        and weapon.can_hit_ships
        and not weapon.shield_only
        and weapon.weapon_range > 0
    )


def _is_starbase(design: Any, hull_id: int | None) -> bool:
    explicit = _value(design, "is_starbase", "isStarbase", default=_MISSING)
    if explicit is not _MISSING:
        return bool(explicit)
    if hull_id is None:
        return False
    hull = stock_hulls().get(int(hull_id))
    return bool(hull and hull.is_starbase)


def _battle_movement(design: Any, slots: list[Any], mass: float, is_starbase: bool) -> float:
    """Apply the published battle-movement equation, in quarter squares."""
    explicit = _value(design, "battle_movement", "combat_speed", default=_MISSING)
    if explicit is not _MISSING:
        return max(0.0, float(explicit) or 0.0)
    if is_starbase:
        return 0.0

    engines = [(s, max(1, _count(s))) for s in slots if _cat(s) == ENGINE and _count(s) > 0]
    if not engines:
        # Incomplete sighting/test data has no engine slot.  Preserve a neutral
        # estimate rather than declaring an otherwise armed hull immobile.
        return 1.0
    engine_count = sum(count for _, count in engines)
    engine_id = _item(engines[0][0])
    ideal_speed = _ENGINE_IDEAL_BATTLE_SPEED.get(engine_id)
    if ideal_speed is None:
        return 1.0

    jets = sum(
        _count(s) for s in slots
        if (_cat(s) == MECHANICAL and _item(s) == 7)
        # The Multi Function Pod acts as a Maneuvering Jet.
        or (_cat(s) == ELECTRICAL and _item(s) == 4)
    )
    overthrusters = sum(_count(s) for s in slots if _cat(s) == MECHANICAL and _item(s) == 8)
    movement = (
        (ideal_speed - 4.0) / 4.0
        - float(mass) / 70.0 / 4.0 / engine_count
        + 0.25 * jets
        + 0.50 * overthrusters
    )
    # The battle board displays and moves in quarter squares, bounded at a
    # quarter square and two-and-a-half squares per round.
    return max(0.25, min(2.5, math.floor(movement * 4.0 + 0.5) / 4.0))


def calculate_offensive_rating(design: Any) -> OffensiveRating:
    """Rate a design's anti-ship offense using public Stars! battle rules.

    Weapon damage is expected one-volley damage: beams are valued at their
    midpoint range (the rules lose 10% only at maximum range), and torpedoes
    include their stated hit probability plus one-eighth shield-only collateral
    damage on misses.  Firing initiative is the hull, battle-computer and weapon
    total.  Movement uses the published engine/mass/jet/overthruster formula.

    The final scalar is intentionally an *offensive* rating, not a battle winner:
    armor, shields, enemy jamming, tactics and target selection remain outside it.
    Its small range, initiative and movement premiums make those board advantages
    visible to the AI while retaining weapon damage as the dominant term.
    """
    slots = list(_value(design, "slots", default=[]) or [])
    hull_id_raw = _value(design, "hull_id", "hullId", default=None)
    hull_id = int(hull_id_raw) if hull_id_raw is not None else None
    is_starbase = _is_starbase(design, hull_id)
    mass = float(_value(design, "mass", default=0) or 0)
    movement = _battle_movement(design, slots, mass, is_starbase)

    explicit_hull_initiative = _value(
        design, "hull_initiative", "base_initiative", "initiative", default=_MISSING
    )
    hull_initiative = (
        float(explicit_hull_initiative)
        if explicit_hull_initiative is not _MISSING
        else float(_STOCK_HULL_INITIATIVE.get(hull_id, 0))
    )
    race_initiative = float(_value(design, "battle_initiative_bonus", "race_initiative_bonus", default=0) or 0)

    computer_initiative = 0.0
    torpedo_remaining_inaccuracy = 1.0
    beam_damage_multiplier = 1.0
    for slot in slots:
        if _cat(slot) != ELECTRICAL:
            continue
        computer = BATTLE_COMPUTERS.get(_item(slot))
        if computer is None:
            continue
        count = max(1, _count(slot))
        computer_initiative += computer[0] * count
        torpedo_remaining_inaccuracy *= (1.0 - computer[1]) ** count
    for slot in slots:
        if _cat(slot) != ELECTRICAL:
            continue
        capacitor_bonus = ENERGY_CAPACITORS.get(_item(slot))
        if capacitor_bonus is not None:
            beam_damage_multiplier += capacitor_bonus * max(1, _count(slot))

    weapon_score = 0.0
    expected_volley = 0.0
    shield_only_volley = 0.0
    weighted_initiative = 0.0
    weighted_range = 0.0
    torpedo_accuracy_weight = 0.0
    torpedo_base_damage = 0.0
    maximum_initiative = 0.0
    weapon_count = 0
    for slot in slots:
        category = _cat(slot)
        if category not in (BEAM, TORPEDO):
            continue
        weapon = _stock_weapon_stats().get((category, _item(slot)))
        if weapon is None or not weapon.can_hit_ships:
            continue
        count = max(1, _count(slot))
        weapon_count += count
        effective_range = weapon.weapon_range + (1.0 if is_starbase else 0.0)
        if category == BEAM:
            # A beam loses a total of 10% damage linearly across range; use the
            # midpoint as the target-neutral expected range.
            expected_damage = (
                weapon.damage * (0.95 if weapon.weapon_range > 0 else 1.0)
                * beam_damage_multiplier
            )
        else:
            accuracy = 1.0 - (1.0 - weapon.accuracy) * torpedo_remaining_inaccuracy
            expected_damage = weapon.damage * (accuracy + (1.0 - accuracy) / 8.0)
            torpedo_accuracy_weight += count * weapon.damage * accuracy
            torpedo_base_damage += count * weapon.damage

        firing_initiative = hull_initiative + race_initiative + computer_initiative + weapon.initiative
        initiative_multiplier = 1.0 + max(0.0, firing_initiative) / 100.0
        # Board range and movement do not change a weapon's damage.  They do,
        # however, increase opportunities to fire before a target escapes or is
        # destroyed.  Keep them deliberately subordinate to expected damage.
        range_multiplier = 1.0 + max(0.0, effective_range) / 20.0
        weapon_score += count * expected_damage * initiative_multiplier * range_multiplier
        expected_volley += count * expected_damage
        if weapon.shield_only:
            shield_only_volley += count * expected_damage
        weighted_initiative += count * expected_damage * firing_initiative
        weighted_range += count * expected_damage * effective_range
        maximum_initiative = max(maximum_initiative, firing_initiative)

    movement_multiplier = 1.0 + min(2.5, movement) / 8.0
    rating = weapon_score * movement_multiplier
    average_initiative = weighted_initiative / expected_volley if expected_volley else 0.0
    average_range = weighted_range / expected_volley if expected_volley else 0.0
    average_torpedo_accuracy = torpedo_accuracy_weight / torpedo_base_damage if torpedo_base_damage else 1.0
    return OffensiveRating(
        rating=rating,
        display_rating=int(math.floor(rating + 0.5)),
        expected_damage_per_volley=expected_volley,
        shield_only_damage_per_volley=shield_only_volley,
        battle_movement=movement,
        average_firing_initiative=average_initiative,
        maximum_firing_initiative=maximum_initiative,
        average_weapon_range=average_range,
        average_torpedo_accuracy=average_torpedo_accuracy,
        is_starbase=is_starbase,
        weapon_count=weapon_count,
    )


def _design_tech_generation(design: Any, tech: dict[str, int] | None = None) -> float:
    explicit = _value(design, "tech_generation", "techGeneration", default=None)
    if explicit is not None:
        return float(explicit)
    if tech:
        vals = [int(v or 0) for v in tech.values()]
        return sum(vals) / max(1, len(vals))
    turn = _value(design, "turn_designed", "turnDesigned", default=None)
    if turn is not None:
        # Not literal tech; useful as a relative recency prior.
        return float(turn) / 10.0
    return 0.0


def evaluate_ship_design(
    design: Any,
    *,
    tech: dict[str, int] | None = None,
    item_db: Any | None = None,
    enemy_profiles: list[ShipCombatProfile] | None = None,
) -> ShipCombatProfile:
    slots = list(_value(design, "slots", default=[]) or [])
    beam = torp = shield = armor = 0.0
    for s in slots:
        b, t, sh, ar = _slot_strength(s, item_db)
        beam += b
        torp += t
        shield += sh
        armor += ar

    mass = float(_value(design, "mass", default=0) or 0)
    armor += float(_value(design, "armor", default=0) or 0)

    resource_cost = float(
        _value(design, "resource_cost", "cost", default=max(1.0, mass * 0.6))
        or max(1.0, mass * 0.6)
    )

    offensive = calculate_offensive_rating(design)
    # These legacy profile fields now retain their equivalent battle-board
    # values, so established callers receive the improved data without a new
    # integration point.
    accuracy = offensive.average_torpedo_accuracy
    initiative = offensive.average_firing_initiative
    combat_speed = offensive.battle_movement
    range_profile = offensive.average_weapon_range

    if beam > 0 and torp > 0:
        doctrine = WeaponDoctrine.MIXED
    elif beam > 0:
        doctrine = WeaponDoctrine.BEAM
    elif torp > 0:
        doctrine = WeaponDoctrine.TORPEDO
    else:
        doctrine = WeaponDoctrine.NONE

    # Keep defense meaningful to fleet-level survivability, but base its
    # offensive term on the new weapon/initiative/movement score.  This makes
    # ship and starbase design selection actually consume the rating.
    offense = offensive.rating
    defense = shield * 0.85 + armor * 0.65
    effective = max(0.0, offense + defense * 0.38)

    tech_generation = _design_tech_generation(design, tech)
    efficiency = effective / max(1.0, resource_cost)

    obsolete = 0.0
    if enemy_profiles:
        enemy_eff = max((p.combat_value_per_resource for p in enemy_profiles), default=0.0)
        enemy_tech = max((p.tech_generation for p in enemy_profiles), default=0.0)
        if enemy_eff > 0:
            efficiency_gap = max(0.0, (enemy_eff - efficiency) / enemy_eff)
            obsolete += 0.65 * min(1.0, efficiency_gap)
        if enemy_tech > tech_generation:
            obsolete += 0.35 * min(1.0, (enemy_tech - tech_generation) / max(1.0, enemy_tech))
        obsolete = min(1.0, obsolete)

    return ShipCombatProfile(
        label=str(_value(design, "name", "design_name", "designName", default="Unnamed design")),
        hull_id=_value(design, "hull_id", "hullId", default=None),
        mass=mass,
        resource_cost=resource_cost,
        beam_strength=beam,
        torpedo_strength=torp,
        shield_strength=shield,
        armor_strength=armor,
        accuracy=accuracy,
        initiative=initiative,
        combat_speed=combat_speed,
        range_profile=range_profile,
        tech_generation=tech_generation,
        doctrine=doctrine,
        effective_combat_value=effective,
        combat_value_per_resource=efficiency,
        obsolete_score=obsolete,
        offensive_rating=offensive.rating,
        display_offensive_rating=offensive.display_rating,
        expected_damage_per_volley=offensive.expected_damage_per_volley,
        battle_movement=offensive.battle_movement,
        firing_initiative=offensive.average_firing_initiative,
        metadata={
            "offensive_rating_model": offensive.model,
            "offensive_rating": offensive.rating,
            "display_offensive_rating": offensive.display_rating,
            "expected_damage_per_volley": offensive.expected_damage_per_volley,
            "shield_only_damage_per_volley": offensive.shield_only_damage_per_volley,
            "battle_movement": offensive.battle_movement,
            "firing_initiative": offensive.average_firing_initiative,
            "maximum_firing_initiative": offensive.maximum_firing_initiative,
            "average_weapon_range": offensive.average_weapon_range,
            "average_torpedo_accuracy": offensive.average_torpedo_accuracy,
            "starbase_weapon_range_bonus": 1 if offensive.is_starbase else 0,
        },
    )


def evaluate_fleet(
    fleet: Any,
    design_lookup: dict[int, ShipCombatProfile],
) -> FleetCombatProfile:
    counts = getattr(fleet, "ship_counts", getattr(fleet, "ship_count", {}))
    if isinstance(counts, list):
        pairs = [(i, int(v or 0)) for i, v in enumerate(counts)]
    elif isinstance(counts, dict):
        pairs = [(int(k), int(v or 0)) for k, v in counts.items()]
    else:
        pairs = []

    total_count = 0
    mass = beam = torp = shield = armor = effective = modern = obsolete = tech_weighted = 0.0
    profiles = []
    for slot, count in pairs:
        if count <= 0 or slot not in design_lookup:
            continue
        p = design_lookup[slot]
        profiles.append(p)
        total_count += count
        mass += p.mass * count
        beam += p.beam_strength * count
        torp += p.torpedo_strength * count
        shield += p.shield_strength * count
        armor += p.armor_strength * count
        effective += p.effective_combat_value * count
        modern += p.effective_combat_value * count * (1.0 - p.obsolete_score)
        obsolete += p.effective_combat_value * count * p.obsolete_score
        tech_weighted += p.tech_generation * count

    if not pairs:
        total_count = int(getattr(fleet, "ship_count_total", 0) or 0)
        mass = float(getattr(fleet, "mass", 0) or 0)
        effective = mass
        modern = effective

    return FleetCombatProfile(
        owner_id=getattr(fleet, "owner_id", getattr(fleet, "owner", None)),
        ship_count=int(total_count),
        total_mass=mass,
        beam_strength=beam,
        torpedo_strength=torp,
        shield_strength=shield,
        armor_strength=armor,
        effective_combat_value=effective,
        modern_combat_value=modern,
        obsolete_combat_value=obsolete,
        average_tech_generation=(tech_weighted / total_count if total_count else 0.0),
        design_profiles=profiles,
    )


def compare_militaries(
    our_fleets: Iterable[FleetCombatProfile],
    enemy_fleets: Iterable[FleetCombatProfile],
) -> RelativeMilitaryAssessment:
    ours = list(our_fleets)
    theirs = list(enemy_fleets)

    our_value = sum(f.effective_combat_value for f in ours)
    enemy_value = sum(f.effective_combat_value for f in theirs)
    our_modern = sum(f.modern_combat_value for f in ours)
    enemy_modern = sum(f.modern_combat_value for f in theirs)
    our_tech_weight = sum(f.average_tech_generation * max(1, f.ship_count) for f in ours)
    enemy_tech_weight = sum(f.average_tech_generation * max(1, f.ship_count) for f in theirs)
    our_ships = sum(max(1, f.ship_count) for f in ours)
    enemy_ships = sum(max(1, f.ship_count) for f in theirs)
    our_avg_tech = our_tech_weight / max(1, our_ships)
    enemy_avg_tech = enemy_tech_weight / max(1, enemy_ships)

    relative = our_value / max(1.0, enemy_value)
    our_modern_fraction = our_modern / max(1.0, our_value)
    enemy_modern_fraction = enemy_modern / max(1.0, enemy_value)

    # Trade ratio penalizes obsolete forces and tech disadvantage.
    tech_modifier = max(0.45, min(1.45, 1.0 + (our_avg_tech - enemy_avg_tech) / 20.0))
    modernization_modifier = max(0.45, min(1.45, (our_modern_fraction + 0.2) / (enemy_modern_fraction + 0.2)))
    trade = relative * tech_modifier * modernization_modifier

    return RelativeMilitaryAssessment(
        our_value=our_value,
        enemy_value=enemy_value,
        relative_strength=relative,
        our_modern_fraction=our_modern_fraction,
        enemy_modern_fraction=enemy_modern_fraction,
        our_avg_tech=our_avg_tech,
        enemy_avg_tech=enemy_avg_tech,
        tech_gap=enemy_avg_tech - our_avg_tech,
        expected_trade_ratio=trade,
    )


def choose_modernization_plan(
    assessment: RelativeMilitaryAssessment,
    *,
    current_design_efficiency: float,
    prospective_design_efficiency: float,
    turns_to_upgrade: int,
    territorial_risk: float,
    sacrifice_budget: float,
    core_at_risk: bool = False,
    research_priorities: list[str] | None = None,
) -> ModernizationPlan:
    research_priorities = research_priorities or ["weapons", "construction", "electronics"]

    improvement = (
        prospective_design_efficiency / max(0.01, current_design_efficiency)
        if current_design_efficiency > 0 else 99.0
    )

    if core_at_risk or territorial_risk > sacrifice_budget + 0.25:
        if assessment.expected_trade_ratio >= 0.85:
            decision = ModernizationDecision.FIGHT_NOW
            reason = "Core/high-value territory is at risk; delaying military response costs more than the modernization gain."
            prod = "Continue current combat production while introducing upgrades as soon as available."
            engage = "Defend critical worlds and accept less-than-ideal trades if required."
        else:
            decision = ModernizationDecision.RETREAT_AND_PRESERVE
            reason = "Core risk is high but current force trades poorly; preserve fleet, concentrate defenses, and rush enabling technology."
            prod = "Minimize obsolete offensive production; build only emergency defensive units."
            engage = "Avoid open battle except at critical defensive positions."
    elif improvement >= 1.35 and assessment.expected_trade_ratio < 0.95 and territorial_risk <= sacrifice_budget:
        decision = ModernizationDecision.TECH_THEN_REBUILD
        reason = "Current ships are being outperformed and the empire can afford limited territorial losses while unlocking a materially better design."
        prod = "Pause or sharply reduce obsolete warship production; bank resources/minerals where useful and prepare replacement queues."
        engage = "Hold core territory, abandon low-value fringe positions if necessary, and avoid decisive fleet actions until modernization."
    elif improvement >= 1.20 and assessment.expected_trade_ratio < 0.75:
        decision = ModernizationDecision.HOLD_AND_TECH
        reason = "Military disadvantage is significant; a short defensive technology phase offers better expected value than reinforcing an inefficient fleet."
        prod = "Build defenses/support vessels selectively; bias research toward the identified combat gap."
        engage = "Delay major engagements and trade space for time outside the core."
    elif assessment.expected_trade_ratio >= 1.10:
        decision = ModernizationDecision.FIGHT_NOW
        reason = "Current forces are expected to trade favorably; there is no strategic need to delay combat for modernization."
        prod = "Continue efficient combat production and reinforce successful designs."
        engage = "Seek favorable battles while preserving concentration of force."
    else:
        decision = ModernizationDecision.BUILD_CURRENT
        reason = "Current designs remain serviceable and the projected upgrade is not large enough to justify a production pause."
        prod = "Continue current production while researching normal progression."
        engage = "Fight selectively based on local force ratios and territorial value."

    return ModernizationPlan(
        decision=decision,
        reason=reason,
        current_design_efficiency=current_design_efficiency,
        prospective_design_efficiency=prospective_design_efficiency,
        expected_turns_to_upgrade=max(0, turns_to_upgrade),
        territorial_risk=max(0.0, min(1.0, territorial_risk)),
        sacrifice_budget=max(0.0, min(1.0, sacrifice_budget)),
        research_priorities=research_priorities,
        production_guidance=prod,
        engagement_guidance=engage,
    )


def infer_research_gaps(
    our_tech: dict[str, int],
    enemy_tech: dict[str, int],
    *,
    enemy_profiles: list[ShipCombatProfile] | None = None,
) -> list[tuple[str, float]]:
    """
    Rank fields by urgency for military catch-up.
    """
    weights = {
        "weapons": 1.35,
        "construction": 1.15,
        "electronics": 1.00,
        "propulsion": 0.85,
        "energy": 0.70,
        "biotech": 0.30,
    }
    urgency = {}
    for field, weight in weights.items():
        gap = max(0, int(enemy_tech.get(field, 0)) - int(our_tech.get(field, 0)))
        urgency[field] = gap * weight

    if enemy_profiles:
        beam = sum(p.beam_strength for p in enemy_profiles)
        torp = sum(p.torpedo_strength for p in enemy_profiles)
        shields = sum(p.shield_strength for p in enemy_profiles)
        armor = sum(p.armor_strength for p in enemy_profiles)
        if torp > beam:
            urgency["electronics"] += 1.5  # targeting/jamming counterplay proxy
        if shields > armor:
            urgency["weapons"] += 1.0
        if armor > shields:
            urgency["weapons"] += 0.7
            urgency["construction"] += 0.6

    return sorted(urgency.items(), key=lambda x: x[1], reverse=True)
