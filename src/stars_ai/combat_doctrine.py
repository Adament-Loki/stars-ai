
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable
import math


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


def _cat(slot: Any) -> int:
    return int(getattr(slot, "category", getattr(slot, "category_mask", 0)) or 0)


def _count(slot: Any) -> int:
    return int(getattr(slot, "count", 0) or 0)


def _item(slot: Any) -> int:
    return int(getattr(slot, "item_id", getattr(slot, "itemId", 0)) or 0)


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


def _design_tech_generation(design: Any, tech: dict[str, int] | None = None) -> float:
    explicit = getattr(design, "tech_generation", None)
    if explicit is not None:
        return float(explicit)
    if tech:
        vals = [int(v or 0) for v in tech.values()]
        return sum(vals) / max(1, len(vals))
    turn = getattr(design, "turn_designed", getattr(design, "turnDesigned", None))
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
    slots = list(getattr(design, "slots", []) or [])
    beam = torp = shield = armor = 0.0
    for s in slots:
        b, t, sh, ar = _slot_strength(s, item_db)
        beam += b
        torp += t
        shield += sh
        armor += ar

    mass = float(getattr(design, "mass", 0) or 0)
    armor += float(getattr(design, "armor", 0) or 0)

    resource_cost = float(
        getattr(design, "resource_cost",
            getattr(design, "cost", max(1.0, mass * 0.6)))
        or max(1.0, mass * 0.6)
    )

    # Prefer concrete design metadata if later supplied by MOD parsing.
    accuracy = float(getattr(design, "accuracy", 0.65 if torp > 0 else 0.9) or 0.0)
    initiative = float(getattr(design, "initiative", 1.0) or 1.0)
    combat_speed = float(getattr(design, "combat_speed", 1.0) or 1.0)
    range_profile = float(getattr(design, "weapon_range", 1.0 if beam > 0 else (2.0 if torp > 0 else 0.0)) or 0.0)

    if beam > 0 and torp > 0:
        doctrine = WeaponDoctrine.MIXED
    elif beam > 0:
        doctrine = WeaponDoctrine.BEAM
    elif torp > 0:
        doctrine = WeaponDoctrine.TORPEDO
    else:
        doctrine = WeaponDoctrine.NONE

    # Approximate effectiveness, deliberately transparent and replaceable by
    # exact Stars! battle math later.
    offense = beam * (0.75 + 0.25 * combat_speed) + torp * accuracy * (0.85 + 0.15 * range_profile)
    defense = shield * 0.85 + armor * 0.65
    tempo = max(0.5, 0.75 + 0.15 * initiative + 0.10 * combat_speed)
    effective = max(0.0, (offense * 0.62 + defense * 0.38) * tempo)

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
        label=str(getattr(design, "name", getattr(design, "design_name", "Unnamed design"))),
        hull_id=getattr(design, "hull_id", getattr(design, "hullId", None)),
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
