"""Put the combat chooser and battle simulator on one turn.

``choose_modernization_plan`` decides whether the empire should seek battle.
``simulate_battle`` then vetoes an intercept or attack whose fitted weapons
cannot keep our ships alive. Battle-plan writes stay blocked; the orders
this module allows are ordinary ``move_fleet`` moves.
"""

from __future__ import annotations

from typing import Any

from .battle_simulator import BattleDesign, BattleSide, BattleStack, Weapon, WeaponKind, simulate_battle
from .combat_doctrine import (
    FleetCombatProfile,
    ModernizationDecision,
    choose_modernization_plan,
    compare_militaries,
    evaluate_fleet,
    evaluate_ship_design,
    weapon_stats,
)
from .starsapi_items import BEAM, TORPEDO


_OFFENSIVE = {ModernizationDecision.FIGHT_NOW, ModernizationDecision.BUILD_CURRENT}


def _native(state) -> dict:
    native = getattr(state, "native", None)
    if isinstance(native, dict):
        return native
    return {}


def _profiles(state) -> dict[int, dict]:
    raw = _native(state).get("design_profiles") or []
    rows = raw.values() if isinstance(raw, dict) else raw
    out: dict[int, dict] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("design_number") is not None:
            out[int(row["design_number"])] = row
    return out


def _profile_has_weapon(profile: dict) -> bool:
    for slot in profile.get("components") or profile.get("slots") or []:
        if not isinstance(slot, dict):
            continue
        category = int(slot.get("category", 0) or 0)
        count = int(slot.get("count", 0) or 0)
        if count <= 0 or category not in (BEAM, TORPEDO):
            continue
        stats = weapon_stats(category, int(slot.get("item_id", 0) or 0))
        if stats is not None and stats.can_hit_ships and stats.weapon_range > 0:
            return True
    return False


def _battle_design(profile: dict) -> tuple[BattleDesign, bool]:
    """Return a battle-board design and whether it carries catalog weapons."""
    weapons: list[Weapon] = []
    for slot in profile.get("components") or profile.get("slots") or []:
        if not isinstance(slot, dict):
            continue
        category = int(slot.get("category", 0) or 0)
        item = int(slot.get("item_id", 0) or 0)
        count = int(slot.get("count", 0) or 0)
        if count <= 0 or category not in (BEAM, TORPEDO):
            continue
        stats = weapon_stats(category, item)
        if stats is None or not stats.can_hit_ships or stats.weapon_range <= 0:
            continue
        kind = WeaponKind.TORPEDO if category == TORPEDO else WeaponKind.BEAM
        weapons.append(Weapon(
            kind=kind,
            damage=float(stats.damage),
            range=int(stats.weapon_range),
            initiative=float(stats.initiative),
            accuracy=float(stats.accuracy),
            count=count,
        ))
    catalog = bool(weapons)
    armor = float(profile.get("armor") or 0)
    if not weapons:
        power = max(1.0, float(profile.get("combat_power") or armor or 1.0))
        weapons = [Weapon(WeaponKind.BEAM, power, 2, 1.0, 1.0, 1)]
        armor = max(20.0, armor, power)
    return BattleDesign(
        name=str(profile.get("name") or "design"),
        armor=max(1.0, armor),
        shields=0.0,
        movement=1.0,
        resource_cost=max(1.0, float(profile.get("resource_cost") or armor or 1.0)),
        weapons=weapons,
    ), catalog


def _ship_counts(fleet) -> list[int]:
    raw = (getattr(fleet, "native", {}) or {}).get("ship_count") or getattr(fleet, "ship_count", []) or []
    try:
        return [max(0, int(value or 0)) for value in list(raw)[:16]]
    except TypeError:
        return []


def fleet_battle_side(name: str, fleets: list, profiles: dict[int, dict]) -> tuple[BattleSide, bool]:
    """Stack the fleets. The flag is true when a catalog weapon was used."""
    stacks: list[BattleStack] = []
    catalog = False
    for fleet in fleets:
        counts = _ship_counts(fleet)
        used = False
        for slot, count in enumerate(counts):
            if count <= 0 or slot not in profiles:
                continue
            design, fitted = _battle_design(profiles[slot])
            if not fitted:
                continue
            stacks.append(BattleStack(design, count))
            catalog = True
            used = True
        if used:
            continue
        power = float(getattr(fleet, "combat_power", 0.0) or 0.0)
        if power <= 0.0:
            continue
        design, _fitted = _battle_design({"name": getattr(fleet, "name", "fleet"), "combat_power": power})
        stacks.append(BattleStack(design, 1))
    return BattleSide(name=name, stacks=stacks), catalog


def simulate_engagement(state, our_fleets: list, enemy_fleets: list):
    """Run one battle. None when neither side has a catalog weapon to judge."""
    profiles = _profiles(state)
    ours, our_catalog = fleet_battle_side("us", our_fleets, profiles)
    theirs, enemy_catalog = fleet_battle_side("them", enemy_fleets, profiles)
    if not ours.stacks or not theirs.stacks:
        return None
    if not our_catalog and not enemy_catalog:
        return None
    return simulate_battle(ours, theirs)


def engagement_favors_us(outcome) -> bool:
    """True when the simulated attacker still has ships and is not losing the trade."""
    if outcome is None:
        return True
    if int(outcome.attacker_survivors) <= 0:
        return False
    if outcome.winner == "us":
        return True
    return float(outcome.expected_trade_ratio) >= 1.0


def _with_counts(fleet):
    native = getattr(fleet, "native", {}) or {}
    fleet.ship_count = native.get("ship_count") or getattr(fleet, "ship_count", [])
    return fleet


def assess_turn_combat(state, plan=None) -> dict[str, Any]:
    """Choose FIGHT / TECH / HOLD / RETREAT / BUILD from visible fleets.

    The result is authoritative only when we have a fitted weapon design.
    Otherwise movement keeps the older strength-ratio rule.
    """
    profiles = _profiles(state)
    armed = [row for row in profiles.values() if _profile_has_weapon(row)]
    if not armed:
        return {
            "decision": ModernizationDecision.BUILD_CURRENT.value,
            "reason": "No fitted weapon design is available to simulate.",
            "authoritative": False,
            "offensive": True,
            "guidance": "",
        }

    lookup = {slot: evaluate_ship_design(row) for slot, row in profiles.items()}
    our_fleets = [
        _with_counts(fleet) for fleet in getattr(state, "fleets", []) or []
        if getattr(fleet, "owner", None) == getattr(state, "player_id", None)
    ]
    enemy_fleets = [
        _with_counts(fleet) for fleet in getattr(state, "fleets", []) or []
        if getattr(fleet, "owner", None) not in (None, getattr(state, "player_id", None))
    ]
    ours = [evaluate_fleet(fleet, lookup) for fleet in our_fleets]
    theirs = [evaluate_fleet(fleet, lookup) for fleet in enemy_fleets]
    if enemy_fleets and sum(profile.effective_combat_value for profile in theirs) <= 0:
        theirs = [
            FleetCombatProfile(
                owner_id=getattr(fleet, "owner", None),
                ship_count=1,
                total_mass=float(getattr(fleet, "combat_power", 0.0) or 0.0),
                beam_strength=0.0,
                torpedo_strength=0.0,
                shield_strength=0.0,
                armor_strength=0.0,
                effective_combat_value=max(1.0, float(getattr(fleet, "combat_power", 0.0) or 0.0)),
                modern_combat_value=max(1.0, float(getattr(fleet, "combat_power", 0.0) or 0.0)),
                obsolete_combat_value=0.0,
                average_tech_generation=0.0,
            )
            for fleet in enemy_fleets
            if float(getattr(fleet, "combat_power", 0.0) or 0.0) > 0.0
        ]
    assessment = compare_militaries(ours, theirs)
    our_profiles = [lookup[slot] for slot in lookup if _profile_has_weapon(profiles[slot])]
    current = max((profile.combat_value_per_resource for profile in our_profiles), default=0.01)
    obsolete = max((profile.obsolete_score for profile in our_profiles), default=0.0)
    prospective = current * (1.0 + obsolete)
    owned = [
        planet for planet in getattr(state, "planets", []) or []
        if getattr(planet, "owner", None) == getattr(state, "player_id", None)
    ]
    home = next((planet for planet in owned if (getattr(planet, "native", {}) or {}).get("is_homeworld")), None)
    core_at_risk = False
    if home is not None and enemy_fleets:
        from .util import distance
        core_at_risk = any(distance(fleet.position, home.position) <= 40.0 for fleet in enemy_fleets)
    near = 0
    if owned and enemy_fleets:
        from .util import distance
        radius = float(getattr(plan, "defense_radius", 100.0) or 100.0)
        for fleet in enemy_fleets:
            if min(distance(fleet.position, planet.position) for planet in owned) <= radius:
                near += 1
    territorial_risk = min(1.0, near / max(1, len(owned)))
    if not enemy_fleets:
        decision = ModernizationDecision.BUILD_CURRENT
        reason = "No enemy fleet is visible, so combat production stays on the existing rule."
        guidance = "Fight selectively based on local force ratios and territorial value."
    else:
        chosen = choose_modernization_plan(
            assessment,
            current_design_efficiency=current,
            prospective_design_efficiency=prospective,
            turns_to_upgrade=6 if obsolete >= 0.35 else 12,
            territorial_risk=territorial_risk,
            sacrifice_budget=0.45,
            core_at_risk=core_at_risk,
        )
        decision = chosen.decision
        reason = chosen.reason
        guidance = chosen.engagement_guidance
    return {
        "decision": decision.value,
        "reason": reason,
        "guidance": guidance,
        "authoritative": True,
        "offensive": decision in _OFFENSIVE,
        "expected_trade_ratio": round(float(assessment.expected_trade_ratio), 3),
        "core_at_risk": core_at_risk,
    }


def publish_turn_combat(state, plan=None) -> dict[str, Any]:
    """Store this turn's combat decision where later planners can read it."""
    report = assess_turn_combat(state, plan)
    if isinstance(getattr(state, "native", None), dict):
        state.native["turn_combat"] = report
    return report


def offensive_posture(state) -> bool:
    """Whether new intercepts and attacks are allowed this turn."""
    report = _native(state).get("turn_combat") or {}
    if not report.get("authoritative"):
        return True
    return bool(report.get("offensive", True))


def engagement_allowed(state, our_fleets: list, enemy_fleets: list) -> bool:
    """Doctrine first, then a simulated survival check when weapons are known."""
    if not offensive_posture(state):
        return False
    report = _native(state).get("turn_combat") or {}
    if not report.get("authoritative"):
        return True
    if not enemy_fleets:
        return True
    return engagement_favors_us(simulate_engagement(state, our_fleets, enemy_fleets))
