"""Critical planets are the worlds the empire actually fortifies.

The homeworld is first. Up to ten owned worlds, and at least every world
when the empire is still smaller than that, get the minerals and the
research. The fortification plan starts at turn 35, not before. By turns
40-50 those worlds should be building a starbase whose weapons are at least
Delta Torpedoes, the first line torpedo that requires Weapons 10. Stars has
no component named Gamma; Delta is that class. The construction window counts
both the resource rate and minerals imported from the homeworld and nearby
worlds, not a fresh mine queue. Once the station is up, that world exports
minerals and population. Minelayers screen those stations afterwards.
"""
from __future__ import annotations

import math

from .combat_doctrine import is_line_weapon
from .models import GameState
from .research_capabilities import ResearchCapability
from .research_cost import research_turns
from .research_planner import ResearchDemand
from .starsapi_items import (
    TORPEDO,
    proven_available_components,
    stock_component_database,
)

FORTRESS_PLAN_TURN = 35
FORTRESS_TURN = 40
FORTRESS_WINDOW_END = 50
FORTRESS_MISSILE_TURN = 50
FORTRESS_BUILD_SPAN = 8
FORTRESS_PHASE_TURNS = 2
FORTRESS_WEAPONS = 10
FORTRESS_MISSILE_WEAPONS = 12
FORTRESS_IMPORT_RANGE = 200
CRITICAL_ASSET_LIMIT = 10
_FIELDS = ("energy", "weapons", "propulsion", "construction", "electronics", "biotechnology")


def clock_turn(state) -> int:
    return max(0, int(getattr(state, "year", 2400) or 2400) - 2400)


def fortress_program_active(state) -> bool:
    return clock_turn(state) >= FORTRESS_TURN


def fortress_plan_active(state) -> bool:
    """True while the empire should be aiming at the turn 40-50 stations."""
    turn = clock_turn(state)
    return FORTRESS_PLAN_TURN <= turn <= FORTRESS_WINDOW_END


def select_critical_planets(state) -> list:
    """Homeworld first, then the most valuable owned worlds, at most ten."""
    owned = [
        planet for planet in getattr(state, "planets", []) or []
        if getattr(planet, "owner", None) == getattr(state, "player_id", None)
    ]

    def rank(planet) -> tuple:
        native = planet.native or {}
        yard = bool((native.get("starbase_capabilities") or {}).get("can_build_ships"))
        return (
            1 if native.get("is_homeworld") else 0,
            int(planet.population or 0),
            int(getattr(planet, "habitability", 0) or 0),
            1 if yard else 0,
            int(getattr(planet, "factories", 0) or 0),
            -int(planet.id),
        )

    ranked = sorted(owned, key=rank, reverse=True)
    return ranked[: min(CRITICAL_ASSET_LIMIT, len(ranked))]


def minimum_fortress_torpedo():
    """The cheapest line torpedo that requires Weapons 10.

    That component is Delta Torpedo. Alpha and Beta are below the floor.
    """
    candidates = [
        spec for spec in stock_component_database().components.values()
        if int(spec.category) == TORPEDO
        and is_line_weapon(spec.category, spec.item_id)
        and "missile" not in spec.name.casefold()
        and int(spec.tech_required[1] or 0) >= FORTRESS_WEAPONS
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda spec: (
            int(spec.tech_required[1] or 0),
            int(spec.tech_required[2] or 0),
            int(spec.item_id),
        ),
    )


def fortress_weapon_ready(state) -> bool:
    """True when Weapons is at least 10 and a Delta-class weapon is researched."""
    if int(getattr(getattr(state, "tech", None), "weapons", 0) or 0) < FORTRESS_WEAPONS:
        return False
    proven = proven_available_components(state)
    return any(
        int(spec.category) == TORPEDO
        and is_line_weapon(spec.category, spec.item_id)
        and int(spec.tech_required[1] or 0) >= FORTRESS_WEAPONS
        for spec in proven.values()
    )


def profile_meets_fortress_weapon(profile: dict) -> bool:
    """True when every filled weapon slot is a Weapons-10 torpedo or missile.

    A laser station, and an Alpha or Beta station, do not qualify.
    """
    database = stock_component_database()
    armed = False
    for slot in profile.get("slots") or []:
        category = int(slot.get("category", 0) or 0)
        item_id = int(slot.get("item_id", 0) or 0)
        count = int(slot.get("count", 0) or 0)
        if count <= 0:
            continue
        beam = bool(category & 0x0010)
        torpedo = bool(category & 0x0020)
        if beam and not torpedo:
            return False
        if not torpedo or beam:
            continue
        spec = database.component(category, item_id)
        if spec is None or int(spec.tech_required[1] or 0) < FORTRESS_WEAPONS:
            return False
        if not is_line_weapon(spec.category, spec.item_id):
            return False
        armed = True
    return armed


def planet_has_fortress_station(state, planet) -> bool:
    """True when the station already in orbit meets the Delta-class floor."""
    native = getattr(planet, "native", {}) or {}
    if not bool(native.get("has_starbase", native.get("starbase", False))):
        return False
    slot = native.get("starbase_design")
    if slot is None:
        return False
    for profile in (getattr(state, "native", {}) or {}).get("starbase_profiles", []) or []:
        if int(profile.get("design_number", -1) or -1) != int(slot):
            continue
        return profile_meets_fortress_weapon(profile)
    return False


def capital_missile_ladder() -> list:
    """Jihad, then each later capital missile, in research order."""
    ladder = [
        spec for spec in stock_component_database().components.values()
        if int(spec.category) == TORPEDO
        and "missile" in spec.name.casefold()
        and is_line_weapon(spec.category, spec.item_id)
        and int(spec.tech_required[1] or 0) >= FORTRESS_MISSILE_WEAPONS
    ]
    return sorted(
        ladder,
        key=lambda spec: (
            int(spec.tech_required[1] or 0),
            int(spec.tech_required[2] or 0),
            int(spec.item_id),
        ),
    )


def best_researched_capital_missile(state):
    proven = proven_available_components(state)
    researched = [spec for spec in capital_missile_ladder() if (int(spec.category), int(spec.item_id)) in proven]
    return researched[-1] if researched else None


def next_capital_missile(state):
    proven = proven_available_components(state)
    for spec in capital_missile_ladder():
        if (int(spec.category), int(spec.item_id)) not in proven:
            return spec
    return None


def _installed_profile(state, planet) -> dict | None:
    native = getattr(planet, "native", {}) or {}
    slot = native.get("starbase_design")
    if slot is None:
        return None
    for profile in (getattr(state, "native", {}) or {}).get("starbase_profiles", []) or []:
        if int(profile.get("design_number", -1) or -1) == int(slot):
            return profile
    return None


def _profile_weapon_tech(profile: dict) -> int:
    database = stock_component_database()
    levels = []
    for slot in profile.get("slots") or []:
        category = int(slot.get("category", 0) or 0)
        count = int(slot.get("count", 0) or 0)
        if count <= 0 or not (category & 0x0020) or (category & 0x0010):
            continue
        spec = database.component(category, int(slot.get("item_id", 0) or 0))
        if spec is not None:
            levels.append(int(spec.tech_required[1] or 0))
    return max(levels) if levels else 0


def _profile_weapon_count(profile: dict) -> int:
    counts = [
        int(slot.get("count", 0) or 0)
        for slot in profile.get("slots") or []
        if int(slot.get("count", 0) or 0) > 0 and int(slot.get("category", 0) or 0) & 0x0020
    ]
    return max(counts) if counts else 0


def installed_trails_desired_weapon(state, planet) -> bool:
    """True when the orbiting station is still a step behind the missile plan.

    Delta is enough until a capital missile exists. After that, Jihad replaces
    Delta, and each later missile replaces the one before it. A thin fit,
    under 12 in a weapon slot, is also still a step behind.
    """
    profile = _installed_profile(state, planet)
    if profile is None or not profile_meets_fortress_weapon(profile):
        return True
    best = best_researched_capital_missile(state)
    if best is not None and int(best.tech_required[1] or 0) > _profile_weapon_tech(profile):
        return True
    return _profile_weapon_count(profile) < 12


def publish_critical_planets(state) -> list:
    """Remember the critical set and mark each owned world.

    The first armed station may export minerals and population. It stays on
    the upgrade list until it carries the best capital missile already
    researched, fitted past the thin first phase.
    """
    chosen = select_critical_planets(state)
    chosen_ids = {int(planet.id) for planet in chosen}
    native = state.native if isinstance(state.native, dict) else {}
    native["critical_planet_ids"] = [int(planet.id) for planet in chosen]
    state.native = native
    for planet in getattr(state, "planets", []) or []:
        if getattr(planet, "owner", None) != getattr(state, "player_id", None):
            continue
        planet_native = dict(planet.native or {})
        supplier = int(planet.id) in chosen_ids and planet_has_fortress_station(state, planet)
        trails = supplier and installed_trails_desired_weapon(state, planet)
        planet_native["fortress_supplier"] = supplier
        # A finished first station may export and still be a critical upgrade
        # target while a better missile, or a thicker fit, is the plan.
        planet_native["critical_asset"] = (
            clock_turn(state) >= FORTRESS_PLAN_TURN
            and int(planet.id) in chosen_ids
            and (not supplier or trails)
        )
        planet.native = planet_native
    return chosen


def _requirements(tech_required) -> dict[str, int]:
    return {
        field: int(level)
        for field, level in zip(_FIELDS, tech_required or ())
        if int(level or 0) > 0
    }


def release_blocked_fortress_design(state) -> None:
    """Let a missing first-hull station design be proposed again.

    A missing same-hull seed at turn 0 used to mark the defense station
    blocked forever. From turn 35 on, that reason is stale: the writer can
    create the first hull with an empty baseline body.
    """
    if clock_turn(state) < FORTRESS_PLAN_TURN:
        return
    native = state.native if isinstance(state.native, dict) else {}
    ledger = dict(native.get("capability_execution") or {})
    entry = ledger.get("design:starbase:defense")
    if not isinstance(entry, dict) or entry.get("status") != "BLOCKED_NATIVE":
        return
    reason = str(entry.get("blocking_reason") or "").casefold()
    if "baseline" not in reason and "seed" not in reason:
        return
    ledger.pop("design:starbase:defense", None)
    native["capability_execution"] = ledger
    state.native = native


def _mineral_output(planet, economy) -> dict:
    from .planet_economy import estimated_mineral_output

    return estimated_mineral_output(planet, economy).get("estimated_mineral_output") or {}


def fortress_import_sources(state, planet) -> list:
    """Homeworld, nearby lesser worlds, and finished fortresses.

    A critical world that is still paying for its own station is not a donor.
    The homeworld is, because its surplus is the first shipment.
    """
    from .util import distance

    donors = []
    for other in getattr(state, "planets", []) or []:
        if getattr(other, "owner", None) != getattr(state, "player_id", None):
            continue
        if int(other.id) == int(planet.id):
            continue
        native = getattr(other, "native", {}) or {}
        if bool(native.get("fortress_supplier")) or bool(native.get("is_homeworld")):
            donors.append(other)
            continue
        if bool(native.get("critical_asset")):
            continue
        if distance(planet.position, other.position) <= FORTRESS_IMPORT_RANGE:
            donors.append(other)
    return donors


def fortress_funding_years(state, planet, deficit: dict) -> int | None:
    """Years to gather one mineral bill from local mines plus imports.

    Current surplus on the homeworld and nearby donors counts immediately.
    Their mine rates, plus this world's own rate, pay whatever is still short.
    A missing rate means the bill cannot be dated. The answer is another
    donor, not another mine on this world.
    """
    from .planet_economy import (
        decode_race_economy,
        mineral_surface_stock,
        working_mineral_reserve,
    )

    economy = decode_race_economy(getattr(state, "race", None))
    local = _mineral_output(planet, economy)
    donors = fortress_import_sources(state, planet)
    years: list[int] = []
    for mineral, need in (deficit or {}).items():
        short = max(0, int(need or 0))
        if short <= 0:
            continue
        immediate = 0
        rate = int(local.get(mineral) or 0)
        for donor in donors:
            stock = mineral_surface_stock(donor)
            reserve = working_mineral_reserve(donor, economy, queue=())
            immediate += max(0, int(stock.get(mineral, 0) or 0) - int(reserve.get(mineral, 0) or 0))
            rate += int(_mineral_output(donor, economy).get(mineral) or 0)
        left = max(0, short - immediate)
        if left <= 0:
            years.append(1)
            continue
        if rate <= 0:
            return None
        years.append(int(math.ceil(left / rate)))
    return max(years) if years else 0


def fortress_construction_outlook(state, planet, deficit: dict, resource_remaining: int) -> dict:
    """Years to spend the resources and years to gather the minerals.

    Both have to fit the fortification window. Minerals include imports.
    """
    from .planet_economy import decode_race_economy, estimated_operating_resources

    mineral_years = fortress_funding_years(state, planet, deficit)
    economy = decode_race_economy(getattr(state, "race", None))
    energy = int(getattr(getattr(state, "tech", None), "energy", 0) or 0)
    produced = int(
        estimated_operating_resources(planet, economy, energy_tech=energy).get("estimated_resources") or 0
    )
    remaining = max(0, int(resource_remaining or 0))
    if remaining <= 0:
        resource_years: int | None = 0
    elif produced <= 0:
        resource_years = None
    else:
        resource_years = int(math.ceil(remaining / produced))
    if mineral_years is None or resource_years is None:
        window = None
    else:
        window = max(int(mineral_years), int(resource_years))
    return {
        "mineral_years": mineral_years,
        "resource_years": resource_years,
        "window_years": window,
        "import_from": [str(getattr(donor, "name", donor.id)) for donor in fortress_import_sources(state, planet)],
    }


def fortress_research_demand(state: GameState) -> ResearchDemand | None:
    """From turn 35, Weapons 10 and Delta Torpedo outrank other research.

    The design is not valid below that floor. Once Delta or a stronger line
    torpedo is already researched, construction is not held for a later weapon.
    """
    native = state.native if isinstance(state.native, dict) else {}
    native["fortress_research_hold"] = False
    state.native = native
    if not fortress_plan_active(state) or clock_turn(state) >= FORTRESS_WINDOW_END:
        return None
    if fortress_weapon_ready(state):
        return None
    spec = minimum_fortress_torpedo()
    if spec is None:
        return None
    requirements = _requirements(spec.tech_required)
    turns, _cost, _budget = research_turns(state, requirements)
    native["fortress_research_hold"] = True
    native["fortress_research_name"] = spec.name
    return ResearchDemand(
        ResearchCapability(
            capability_id=f"fortress-weapon:{spec.category}:{spec.item_id}",
            name=spec.name,
            category="combat",
            requirements=requirements,
            post_unlock_action=(
                f"Design the critical-planet starbase with {spec.name} in every weapon slot. "
                "Weapons below 10 does not validate that design."
            ),
            source="critical planet fortress",
            tags=("fortress", "weapon"),
            unlocks=(spec.name,),
            enables_roles=("starbase",),
            expiry_condition="Weapons is at least 10 and Delta Torpedo or a stronger line torpedo is researched.",
        ),
        need=3.4,
        urgency=2.6,
        utilization=1.0,
        value=3.0,
        military_emergency=True,
        explanation=(
            f"Turn {clock_turn(state)} is inside the fortification plan and Weapons is "
            f"{int(getattr(state.tech, 'weapons', 0) or 0)}. {spec.name} is the minimum "
            f"weapon class and is {turns} turn(s) away at the current research rate. "
            "Research it before those stations are designed."
        ),
    )


def capital_missile_demand(state: GameState) -> ResearchDemand | None:
    """From turn 50, the next capital missile is the starbase upgrade.

    Jihad replaces Delta. Each later missile replaces the one before it.
    The demand does not hold the station that can be built with today's weapon.
    """
    if clock_turn(state) < FORTRESS_MISSILE_TURN:
        return None
    spec = next_capital_missile(state)
    if spec is None:
        return None
    requirements = _requirements(spec.tech_required)
    turns, _cost, _budget = research_turns(state, requirements)
    previous = best_researched_capital_missile(state)
    previous_name = previous.name if previous is not None else "Delta Torpedo"
    return ResearchDemand(
        ResearchCapability(
            capability_id=f"fortress-missile:{spec.category}:{spec.item_id}",
            name=spec.name,
            category="combat",
            requirements=requirements,
            post_unlock_action=(
                f"Refit critical starbases from {previous_name} to {spec.name}, "
                "starting with a partial weapon and shield fit that can be built soon."
            ),
            source="critical planet fortress",
            tags=("fortress", "weapon", "missile"),
            unlocks=(spec.name,),
            enables_roles=("starbase",),
            expiry_condition="This capital missile is researched. The next missile in the ladder then takes its place.",
        ),
        need=3.6,
        urgency=2.8,
        utilization=1.0,
        value=3.2,
        military_emergency=turns <= 8,
        explanation=(
            f"Turn {clock_turn(state)} is past the first fortification window. "
            f"{spec.name} is the next starbase weapon after {previous_name} and is "
            f"{turns} turn(s) away. Plan the station upgrade for when it arrives."
        ),
    )
