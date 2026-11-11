from __future__ import annotations

from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path
import csv
import math

from .colony_planner import colony_planet_is_eligible
from .diplomacy import allied_player_ids, world_faces_hostile
from .empire_geometry import distance_from_homeworld
from .expansion_network import evaluate_expansion_network
from .planet_economy import (
    decode_race_economy,
    estimated_operating_resources,
    mineral_surface_stock,
    working_mineral_reserve,
)
from .combat_doctrine import calculate_offensive_rating
from .native.resources import native_resource_path
from .starsapi_items import stock_component_database
from .territorial_defense import assess_territorial_defense
from .util import distance
from .capability_execution import design_is_blocked, profile_is_blocked

STARBASE_QUEUE_SLOT_OFFSET = 16
STARBASE_DESIGN_LIMIT = 10
MAX_NEW_SUPPORT_BASES_PER_TURN = 2

# Alternate Reality citizens live in orbit, so these are habitat capacities as
# well as starbase hull statistics.  They are intentionally kept with the base
# planner rather than inferred from a planet's normal habitability.
AR_STARBASE_POPULATION_CAPACITY = {
    32: 250_000,   # Orbital Fort / starter habitat
    33: 500_000,   # Space Dock
    34: 1_000_000, # Space Station
    35: 2_000_000, # Ultra Station
    36: 3_000_000, # Death Star
}


@dataclass(frozen=True)
class SupportBaseBuild:
    planet_id: int
    design_slot: int
    design_name: str
    hull_name: str
    priority: int
    reason: str
    complete_percent: int = 0

    def queue_item(self) -> dict:
        item = {
            "item": "starbase_design",
            "design_slot": self.design_slot,
            "design_name": self.design_name,
            "hull_name": self.hull_name,
            "quantity": 1,
            "role": "fuel_hub",
        }
        if self.complete_percent:
            item["complete_percent"] = self.complete_percent
        return item


@dataclass(frozen=True)
class SupportBaseMaterialDemand:
    """The real remaining bill for a proposed or stalled support base.

    A starbase must not reserve the front of a world queue merely because it is
    strategically desirable.  This record makes the bill visible to the queue
    planner and to freight routing, including partial construction already
    paid for by the host.
    """
    planet_id: int
    design_slot: int
    design_name: str
    complete_percent: int
    resource_remaining: int
    mineral_remaining: dict[str, int]
    mineral_required: dict[str, int]
    mineral_deficit: dict[str, int]
    ready: bool
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def _production_for(state, planet_id: int) -> list[dict]:
    raw = (state.native or {}).get("production_by_planet", {}) or {}
    return list(raw.get(str(int(planet_id)), raw.get(int(planet_id), [])) or [])


def _queued_starbase_item(state, planet_id: int) -> dict | None:
    for item in _production_for(state, planet_id):
        if int(item.get("item_type", 0) or 0) != 4:
            continue
        item_id = int(item.get("item_id", -1) or -1)
        if STARBASE_QUEUE_SLOT_OFFSET <= item_id < STARBASE_QUEUE_SLOT_OFFSET + STARBASE_DESIGN_LIMIT:
            return item
    return None


def _support_designs(state) -> dict[int, dict]:
    """Only already-defined race-legal designs with real shipyard+refuel capability."""
    return {
        int(profile["design_number"]): profile
        for profile in (state.native or {}).get("starbase_profiles", []) or []
        if not profile_is_blocked(state,profile,is_starbase=True)
        if bool((profile.get("capabilities") or {}).get("can_refuel"))
        and bool((profile.get("capabilities") or {}).get("can_build_ships"))
    }


def _preferred_support_design(state, *, prefer_defensive: bool = False) -> dict | None:
    designs = list(_support_designs(state).values())
    if not designs:
        return None
    lrts = {str(x).upper() for x in ((state.race.native or {}).get("lrts", []) or [])}
    has_isb = "ISB" in lrts
    # A Space Dock can only exist for an ISB race. If corrupted/synthetic input
    # claims otherwise, refuse to prefer it. Normal races reuse Space Station or
    # another already-defined legal shipyard/refuel design.
    legal = [d for d in designs if has_isb or int(d.get("hull_id", -1)) not in (33, 35)]
    if not legal:
        return None
    if prefer_defensive:
        armed = [design for design in legal if calculate_offensive_rating(design).weapon_count > 0]
        if armed:
            return max(
                armed,
                key=lambda design: (
                    calculate_offensive_rating(design).rating,
                    calculate_offensive_rating(design).expected_damage_per_volley,
                    int(design.get("hull_id", -1) or -1),
                    -int(design.get("design_number", 999)),
                ),
            )
    return min(
        legal,
        key=lambda d: (
            0 if has_isb and int(d.get("hull_id", -1)) == 33 else 1,
            0 if int(d.get("hull_id", -1)) == 34 else 1,
            int(d.get("hull_id", 999)),
            int(d.get("design_number", 999)),
        ),
    )


def _cheapest_support_design(state) -> dict | None:
    """The legal shipyard/refuel design with the smallest bill and fewest weapons.

    Quiet allied-border worlds use this instead of an armed station. A hostile
    border still uses the normal or defensive choice.
    """

    designs = list(_support_designs(state).values())
    lrts = {str(item).upper() for item in ((state.race.native or {}).get("lrts", []) or [])}
    has_isb = "ISB" in lrts
    legal = [design for design in designs if has_isb or int(design.get("hull_id", -1)) not in (33, 35)]
    if not legal:
        return None
    return min(
        legal,
        key=lambda design: (
            int(support_base_design_cost(design).get("resources", 0) or 0),
            _starbase_weapon_count(design),
            int(design.get("hull_id", 999) or 999),
            int(design.get("design_number", 999) or 999),
        ),
    )


def _planet_is_key_world(state, planet, network) -> bool:
    """A world worth a defensive starbase hub, not a cheap fuel stop."""
    native = planet.native or {}
    if bool(native.get("is_homeworld")):
        return True
    if world_faces_hostile(state, planet):
        return True
    hub = next((item for item in network.hubs if int(item.planet_id) == int(planet.id)), None)
    if hub is not None and (hub.designated_layer1 or int(getattr(hub, "ring", 9) or 9) <= 1):
        return True
    return int(planet.population or 0) >= 250_000


def _planet_attack_pressure(state, planet, territorial) -> int:
    """How often this world is the one enemies are actually leaning on."""
    planet_id = int(planet.id)
    violations = getattr(territorial, "violations", ()) or ()
    anchored = sum(
        1 for violation in violations
        if int(getattr(violation, "anchor_planet_id", -1)) == planet_id
    )
    native = planet.native or {}
    lost = 3 if native.get("recently_lost_by_player") else 0
    facing = 2 if world_faces_hostile(state, planet) else 0
    return anchored * 2 + lost + facing


def _lacks_shipyard_hub(planet) -> bool:
    caps = (planet.native or {}).get("starbase_capabilities") or {}
    return not (bool(caps.get("can_refuel")) and bool(caps.get("can_build_ships")))


def _is_alternate_reality_player(state) -> bool:
    race = getattr(state, "race", None)
    native = getattr(race, "native", {}) or {}
    try:
        prt_id = int(native.get("prt_id", -1))
    except (TypeError, ValueError):
        prt_id = -1
    prt = str(getattr(race, "primary_trait", "") or "").casefold()
    return prt_id == 8 or "alternate reality" in prt


REFUEL_FORT_MIN_POPULATION = 10_000


def _armed_refuel_fort(state) -> dict | None:
    """An Orbital Fort whose weapons are what make it a fuel stop."""
    chosen = None
    for profile in (state.native or {}).get("starbase_profiles", []) or []:
        if profile_is_blocked(state, profile, is_starbase=True):
            continue
        if int(profile.get("hull_id", -1) or -1) != 32:
            continue
        if not bool((profile.get("capabilities") or {}).get("can_refuel")):
            continue
        chosen = profile
    return chosen


def _queue_refuel_forts(state, builds: list[SupportBaseBuild]) -> list[SupportBaseBuild]:
    """Put an armed Orbital Fort on a non-AR colony that can already grow.

    An empty dock or station is not a fuel stop. The fort waits until a
    jump-start has put 10,000 people on a livable world, then two of those
    worlds may start one per turn.
    """
    if _is_alternate_reality_player(state):
        return builds
    design = _armed_refuel_fort(state)
    if design is None:
        return builds
    claimed = {int(item.planet_id) for item in builds}
    worlds = []
    for planet in state.planets:
        if planet.owner != state.player_id or int(planet.id) in claimed:
            continue
        if bool((planet.native or {}).get("is_homeworld")):
            continue
        caps = (planet.native or {}).get("starbase_capabilities") or {}
        if bool(caps.get("can_refuel")) or bool(caps.get("can_build_ships")):
            continue
        if _queued_starbase_item(state, int(planet.id)) is not None:
            continue
        pop = int(planet.population or 0)
        hab = int(planet.habitability or 0)
        if pop < REFUEL_FORT_MIN_POPULATION or hab < 40:
            continue
        worlds.append((hab, pop, planet))
    worlds.sort(key=lambda row: (-row[0], -row[1], int(row[2].id)))
    out = list(builds)
    added = 0
    for hab, pop, planet in worlds:
        if added >= MAX_NEW_SUPPORT_BASES_PER_TURN:
            break
        out.append(SupportBaseBuild(
            planet_id=int(planet.id),
            design_slot=int(design["design_number"]),
            design_name=str(design.get("name") or "Refuel Fort"),
            hull_name=str(design.get("hull_name") or "Orbital Fort"),
            priority=158,
            reason=(
                f"{planet.name} is {hab}% habitable with {pop:,} people and no fuel stop. "
                "An armed Orbital Fort refuels here. An empty starbase would not."
            ),
        ))
        added += 1
    return out


def _starbase_weapon_count(profile: dict | None) -> int:
    """Return fitted orbital weapons, rather than treating hull size as defense."""
    return sum(
        max(0, int(slot.get("count", 0) or 0))
        for slot in ((profile or {}).get("slots", []) or [])
        if int(slot.get("category", 0) or 0) in (16, 32)
    )


def _starbase_defense_strength(profile: dict | None) -> float:
    """Return a defensive selection value led by actual orbital offense.

    The stock battle-board offensive rating handles weapon damage, beam range,
    torpedo accuracy, initiative, battle computers and the starbase +1 range
    rule.  The remaining terms preserve the existing hull/utility protection
    preference; this is a design-selection value, not a combat resolver.
    """
    profile = profile or {}
    weapons = _starbase_weapon_count(profile)
    fittings = sum(
        max(0, int(slot.get("count", 0) or 0))
        for slot in (profile.get("slots", []) or [])
    )
    hull_id = int(profile.get("hull_id", -1) or -1)
    hull_cost = _stock_starbase_hull_costs().get(hull_id, {})
    offensive = calculate_offensive_rating(profile).rating
    # The stock hull's resource bill is a stable proxy for its inherent armor
    # when compact native profile records do not expose armor directly.
    hull_protection = math.sqrt(max(0, int(hull_cost.get("resources", 0) or 0)))
    return offensive + 0.65 * max(0, fittings - weapons) + hull_protection


def _alternate_reality_habitat_gain(planet, current_hull: int, target_hull: int, economy) -> tuple[float, dict]:
    """Return the economic value of an AR base upgrade at this specific world."""
    if not bool(economy.alternate_reality):
        return 0.0, {}
    current_capacity = int(AR_STARBASE_POPULATION_CAPACITY.get(current_hull, 0))
    target_capacity = int(AR_STARBASE_POPULATION_CAPACITY.get(target_hull, 0))
    added_capacity = max(0, target_capacity - current_capacity)
    if not current_capacity or not added_capacity:
        return 0.0, {
            "current_capacity": current_capacity,
            "target_capacity": target_capacity,
            "added_capacity": added_capacity,
        }
    # Capacity matters most where the present orbital habitat is becoming
    # crowded; an empty new colony should not rush a Death Star merely because
    # the hull is available.  Express the gain in 100k-colonist habitats so it
    # can be compared transparently with the defensive readiness score.
    population_pressure = max(0.0, float(int(planet.population or 0)) / current_capacity)
    economic_value = (added_capacity / 100_000.0) * population_pressure
    return economic_value, {
        "current_capacity": current_capacity,
        "target_capacity": target_capacity,
        "added_capacity": added_capacity,
        "population_pressure": population_pressure,
    }


def _profile_is_torpedo_fortress(profile: dict) -> bool:
    """True when the station's weapons are at least Delta Torpedo."""
    from .critical_assets import profile_meets_fortress_weapon

    return profile_meets_fortress_weapon(profile)


def _torpedo_fortress_design(state) -> dict | None:
    from .critical_assets import FORTRESS_WEAPONS

    if int(getattr(state.tech, "weapons", 0) or 0) < FORTRESS_WEAPONS:
        return None
    designs = [
        design for design in _support_designs(state).values()
        if _profile_is_torpedo_fortress(design)
    ]
    if not designs:
        return None
    return max(
        designs,
        key=lambda design: (
            int(design.get("hull_id", -1) or -1),
            -int(design.get("design_number", 999) or 999),
        ),
    )


def _planet_fortress_patience(state, planet) -> int:
    """How many years this world can wait for a better station.

    A valuable, quiet world can wait for a larger hull. A small or threatened
    world takes the armed hull it can finish now. Twenty years is never acceptable.
    """
    native = planet.native or {}
    pop = int(planet.population or 0)
    if world_faces_hostile(state, planet):
        return 3
    if bool(native.get("is_homeworld")) or pop >= 200_000:
        return 8
    if pop >= 100_000:
        return 6
    return 4


def _profile_completion_years(state, planet, profile, resource_turns: int | None = None) -> int:
    from .critical_assets import fortress_funding_years

    cost = support_base_design_cost(profile)
    stock = mineral_surface_stock(planet)
    deficit = {
        mineral: max(0, int(cost.get(mineral, 0) or 0) - int(stock.get(mineral, 0) or 0))
        for mineral in ("ironium", "boranium", "germanium")
    }
    mineral_years = fortress_funding_years(state, planet, deficit)
    if resource_turns is None:
        economy = decode_race_economy(state.race)
        produced = int(
            estimated_operating_resources(
                planet, economy, energy_tech=int(state.tech.energy or 0),
            ).get("estimated_resources") or 0
        )
        resources = int(cost.get("resources", 0) or 0)
        resource_turns = 99 if produced <= 0 else max(1, int(math.ceil(resources / produced)))
    if mineral_years is None:
        mineral_years = 99 if any(deficit.values()) else 0
    return max(int(resource_turns), int(mineral_years))


def _profile_weapon_rank(profile: dict) -> tuple[int, int, int, int]:
    """Missile, weapon tech, slot count, hull size. Larger is the later plan."""
    database = stock_component_database()
    missile = 0
    tech = 0
    count = 0
    for slot in profile.get("slots") or []:
        category = int(slot.get("category", 0) or 0)
        filled = int(slot.get("count", 0) or 0)
        if filled <= 0 or not (category & 0x0020) or (category & 0x0010):
            continue
        spec = database.component(category, int(slot.get("item_id", 0) or 0))
        count = max(count, filled)
        if spec is None:
            continue
        tech = max(tech, int(spec.tech_required[1] or 0))
        if "missile" in spec.name.casefold():
            missile = 1
    return missile, tech, count, int(profile.get("hull_id", -1) or -1)


def _fortress_design_for_planet(state, planet) -> dict | None:
    """Pick the station this world should build, not the largest one in the catalog.

    Among designs that finish inside the world's patience, prefer a capital
    missile, then a larger hull, then a thicker fit. If nothing finishes in
    time, take the armed design that finishes soonest. An Orbital Fort this
    year beats a Space Station that is still twenty years away.
    """
    from .critical_assets import FORTRESS_WEAPONS

    if int(getattr(state.tech, "weapons", 0) or 0) < FORTRESS_WEAPONS:
        return None
    designs = [
        design for design in _support_designs(state).values()
        if _profile_is_torpedo_fortress(design)
    ]
    if not designs:
        return None
    patience = _planet_fortress_patience(state, planet)
    ranked = []
    for design in designs:
        years = _profile_completion_years(state, planet, design)
        missile, tech, count, hull = _profile_weapon_rank(design)
        ranked.append((years <= patience, years, missile, tech, hull, count, design))
    fitting = [row for row in ranked if row[0]]
    if fitting:
        return max(fitting, key=lambda row: (row[2], row[3], row[4], row[5], -row[1]))[-1]
    return min(ranked, key=lambda row: (row[1], -row[2], -row[3], -row[4]))[-1]


def _defensive_design_for_world(
    state,
    planet,
    *,
    installed_slot: int | None,
    torpedo_only: bool = False,
) -> tuple[dict | None, dict]:
    """Choose the defensive refit with the best protection-ready-time value.

    A larger base is not automatically better at a threatened world: the old
    base protects the colony while a replacement is being built, so a compact
    armed refit often gives the earlier and higher-value safety improvement.
    A much stronger larger hull can still win once its added protection repays
    its construction delay.
    """
    profiles = list(_support_designs(state).values())
    installed = next(
        (
            profile for profile in (state.native or {}).get("starbase_profiles", []) or []
            if installed_slot is not None
            and int(profile.get("design_number", -1)) == int(installed_slot)
        ),
        None,
    )
    current_strength = _starbase_defense_strength(installed)
    economy = decode_race_economy(state.race)
    resources = max(1, int(estimated_operating_resources(planet, economy, energy_tech=int(state.tech.energy or 0))["estimated_resources"]))
    candidates = []
    for profile in profiles:
        slot = int(profile.get("design_number", -1) or -1)
        if slot < 0 or slot == installed_slot or _starbase_weapon_count(profile) <= 0:
            continue
        if torpedo_only and not _profile_is_torpedo_fortress(profile):
            continue
        cost = recycled_starbase_cost(profile, installed)
        build_turns = max(1, int(math.ceil(int(cost["resources"]) / resources)))
        strength = _starbase_defense_strength(profile)
        habitat_value, habitat = _alternate_reality_habitat_gain(
            planet,
            int((installed or {}).get("hull_id", -1) or -1),
            int(profile.get("hull_id", -1) or -1),
            economy,
        )
        # A design's value is the protection it adds while accounting for the
        # time the existing orbital cover must carry the world on its own. AR
        # capacity is a first-class economic gain, not a cosmetic tie-breaker.
        readiness = (strength - current_strength + habitat_value) / build_turns
        candidates.append((
            readiness, strength, -build_turns, -int(cost["resources"]), -slot,
            profile, build_turns, habitat,
        ))
    if not candidates:
        return None, {
            "current_strength": current_strength,
            "resources": resources,
            "reason": "no armed, already-defined support-base refit is available",
        }
    if torpedo_only:
        patience = _planet_fortress_patience(state, planet)
        def _patient(row):
            profile = row[5]
            years = _profile_completion_years(state, planet, profile, resource_turns=row[6])
            missile, tech, count, hull = _profile_weapon_rank(profile)
            fits = years <= patience
            return (fits, missile, tech, hull, count, -years if fits else -10_000 - years)
        _readiness, strength, _neg_turns, _neg_cost, _slot, profile, build_turns, habitat = max(candidates, key=_patient)
    else:
        _readiness, strength, _neg_turns, _neg_cost, _slot, profile, build_turns, habitat = max(candidates)
    if not torpedo_only and _readiness <= 0:
        return None, {
            "current_strength": current_strength,
            "resources": resources,
            "reason": "no armed refit improves protection enough to repay its construction time",
        }
    return profile, {
        "current_strength": current_strength,
        "target_strength": strength,
        "build_turns": build_turns,
        "resource_cost": int(cost["resources"]),
        "resources": resources,
        "readiness_gain": _readiness,
        "alternate_reality_habitat": habitat,
    }


def desired_support_base_count(state) -> int:
    """Competitive milestone: roughly 3-5 useful support bases by T25-30.

    The target is bounded by empire size so a tiny/bad-start empire does not
    bankrupt itself chasing an arbitrary count.
    """
    owned = [p for p in state.planets if p.owner == state.player_id]
    n = len(owned)
    if n <= 1:
        return 1 if n else 0
    turn = max(0, int(state.year) - 2400)
    if turn < 10:
        milestone = 1
    elif turn < 18:
        milestone = 2
    elif turn < 25:
        milestone = 3
    elif turn < 30:
        milestone = 4
    else:
        milestone = 5
    if turn >= 40:
        milestone = max(milestone, min(8, int(math.ceil(n / 4.0))))
    # About one hub for every two owned worlds is an aggressive but bounded
    # opening network. This makes 3-5 bases feasible when the empire has 6-10+
    # worlds, while small empires get a smaller target.
    empire_bound = max(1, int(math.ceil(n / 2.0)))
    return min(milestone, empire_bound)


def _operational_support_worlds(state):
    return [
        p for p in state.planets
        if p.owner == state.player_id
        and bool(((p.native or {}).get("starbase_capabilities") or {}).get("can_refuel"))
        and bool(((p.native or {}).get("starbase_capabilities") or {}).get("can_build_ships"))
    ]


def _candidate_score(state, planet, operational, plan, network) -> tuple[float, dict]:
    native = planet.native or {}
    caps = native.get("starbase_capabilities") or {}
    has_base = bool(native.get("has_starbase", native.get("starbase", False)))
    is_fort = bool(caps.get("is_orbital_fort"))
    nearest_hub = min(
        (distance(planet.position, hub.position) for hub in operational if int(hub.id) != int(planet.id)),
        default=999.0,
    )
    viable = [
        other for other in state.planets
        if other.owner is None and other.observed
        and colony_planet_is_eligible(state, other, plan)
        and distance(planet.position, other.position) <= 160.0
    ]
    unknown = sum(
        1 for other in state.planets
        if not other.observed and distance(planet.position, other.position) <= 120.0
    )
    economy = decode_race_economy(state.race)
    resources = int(estimated_operating_resources(planet, economy, energy_tech=int(state.tech.energy or 0))["estimated_resources"])
    industry = int(planet.factories or 0) + int(planet.mines or 0)
    home_distance = float(distance_from_homeworld(state, planet.position))
    hub = next((h for h in network.hubs if int(h.planet_id) == int(planet.id)), None)
    ring = int(hub.ring) if hub is not None else 0
    pop = int(planet.population or 0)
    mineral_ready = min(1.0, (
        max(0, int(planet.ironium or 0))
        + max(0, int(planet.boranium or 0))
        + 1.5 * max(0, int(planet.germanium or 0))
    ) / 300.0)
    network_gap = not operational or nearest_hub >= 120.0
    outer_bonus = 1.0 if int(planet.id) in set(network.outer_hub_ids) else 0.0
    layer1_pending = bool(hub and hub.designated_layer1 and not hub.graduated)
    promotion_tier = int(getattr(hub,"promotion_tier",3) or 3) if hub is not None else 3
    promotion_rank = getattr(hub,"promotion_rank",None) if hub is not None else None
    promotion_value = float(getattr(hub,"overall_value",0.0) or 0.0) if hub is not None else 0.0
    # A P2 is deliberately not an active base project until its P1 has
    # graduated. The classification remains visible, but the spending order is
    # HW -> P1 -> active P2.
    next_layer_child = bool(
        hub and promotion_tier == 2 and hub.parent_exporter_id is not None
    )

    score = (
        0.60 * min(1.5, pop / 250_000.0)
        + 0.42 * min(1.5, industry / 220.0)
        + 0.35 * min(1.5, resources / 500.0)
        + 0.46 * min(1.5, nearest_hub / 220.0)
        + 0.22 * min(4, len(viable))
        + 0.045 * min(10, unknown)
        + 0.18 * min(3, ring)
        + 0.22 * min(2.0, home_distance / 180.0)
        + 0.36 * outer_bonus
        + 0.28 * mineral_ready
        + (0.38 if is_fort else 0.0)
        + (0.22 if has_base else 0.0)
        + (0.35 if network_gap else 0.0)
        + (2.20 if layer1_pending else 0.0)
        + (0.95 if next_layer_child else 0.0)
        + 0.70 * promotion_value
    )
    detail = {
        "nearest_hub": nearest_hub,
        "viable": len(viable),
        "unknown": unknown,
        "resources": resources,
        "industry": industry,
        "home_distance": home_distance,
        "ring": ring,
        "is_fort": is_fort,
        "has_base": has_base,
        "network_gap": network_gap,
        "outer": bool(outer_bonus),
        "layer1_pending": layer1_pending,
        "next_layer_child": next_layer_child,
        "promotion_tier": promotion_tier,
        "promotion_rank": promotion_rank,
        "promotion_value": promotion_value,
    }
    return score, detail


def plan_support_base_builds(state, plan=None) -> list[SupportBaseBuild]:
    """Maintain a growing network of high-value shipyard/refuel hubs.

    Existing projects are always continued. If the empire is below its base
    milestone, start up to two additional projects this turn on mature/high-value
    worlds. A newly proposed support design is deliberately not queued in this
    same turn: it must first appear in the next M-file read-back, after which
    this planner treats it exactly like any player-authored starbase design.
    """
    support_designs = _support_designs(state)
    if not support_designs and design_is_blocked(
        state,"starbase",is_starbase=True,purpose="support",
    ):
        state.native["starbase_capability_demand"]={
            "capability_id":"design:starbase:support",
            "status":"BLOCKED_NATIVE",
            "design_available":False,
            "policy":"Do not queue a support base until a legal native design is emitted and appears in an M-file read-back.",
        }
    territorial = assess_territorial_defense(state, plan)
    exposure = territorial.exposure
    exposed_world_ids = set(exposure.border_world_ids)
    # Network expansion still selects the economical support design. Defensive
    # replacement work below evaluates each threatened world's production rate
    # and installed base rather than blindly selecting the largest hull.
    preferred = _preferred_support_design(
        state,
        prefer_defensive=bool(exposure.defensive_base_priority >= 180),
    )
    if preferred is None:
        return _queue_refuel_forts(state, [])

    owned = [p for p in state.planets if p.owner == state.player_id]
    operational = _operational_support_worlds(state)
    network = evaluate_expansion_network(state)
    target = desired_support_base_count(state)
    turn = max(0, int(state.year) - 2400)
    # Onion milestone: by T25-30, build real support bases on roughly four of
    # the designated Layer-1 worlds, then push toward five when the economy can
    # support it. The homeworld/support core is counted separately in the total.
    if turn >= 30:
        layer1_goal = min(5, len(network.layer1_hub_ids))
    elif turn >= 25:
        layer1_goal = min(4, len(network.layer1_hub_ids))
    elif turn >= 18:
        layer1_goal = min(3, len(network.layer1_hub_ids))
    else:
        layer1_goal = min(2, len(network.layer1_hub_ids))
    target = max(target, min(len(owned), 1 + layer1_goal))
    out: list[SupportBaseBuild] = []
    queued_planets: set[int] = set()

    # Continue all recognized support-base projects, not just the first one.
    for planet in owned:
        queued = _queued_starbase_item(state, planet.id)
        if queued is None:
            continue
        slot = int(queued["item_id"]) - STARBASE_QUEUE_SLOT_OFFSET
        profile = support_designs.get(slot)
        if profile is None:
            continue
        queued_planets.add(int(planet.id))
        out.append(SupportBaseBuild(
            planet_id=int(planet.id),
            design_slot=slot,
            design_name=str(profile.get("name") or profile.get("hull_name") or f"Starbase #{slot + 1}"),
            hull_name=str(profile.get("hull_name") or "Support starbase"),
            priority=148,
            complete_percent=int(queued.get("complete_percent", 0) or 0),
            reason=(
                f"continue in-progress support-base project at {planet.name}; native completion="
                f"{int(queued.get('complete_percent', 0) or 0)}%. Empire target is {target} useful "
                "shipyard/refuel bases."
            ),
        ))

    # A base replacement is safe overlap protection: the installed base stays
    # active until the new design completes.  Select at most one replacement
    # at a time, both for an immediate threat and for a newly read-back
    # current-tech design.  The latter is what lets a mature empire retire a
    # starter station with obsolete lasers/shields instead of treating any
    # already-armed base as permanently good enough.
    from .critical_assets import (
        FORTRESS_PLAN_TURN,
        clock_turn,
        fortress_research_demand,
        publish_critical_planets,
    )
    critical_planets = publish_critical_planets(state) if clock_turn(state) >= FORTRESS_PLAN_TURN else []
    if critical_planets:
        fortress_research_demand(state)
    critical_ids = {int(planet.id) for planet in critical_planets}
    fortress_hold = bool(critical_ids and (state.native or {}).get("fortress_research_hold"))
    if owned:
        upgrades = []
        for planet in owned:
            if int(planet.id) in queued_planets:
                continue
            native = planet.native or {}
            if not bool(native.get("has_starbase", native.get("starbase", False))):
                continue
            # A key world is worth a better station even when the neighbor is
            # an ally. Upgrading recycles the base already in orbit, so the
            # incremental bill is the useful comparison, not a new hull.
            if critical_ids:
                if int(planet.id) not in critical_ids:
                    continue
            elif not _planet_is_key_world(state, planet, network):
                continue
            if fortress_hold and not _lacks_shipyard_hub(planet):
                continue
            installed_slot = native.get("starbase_design")
            installed_slot = None if installed_slot is None else int(installed_slot)
            defensive_design, assessment = _defensive_design_for_world(
                state, planet, installed_slot=installed_slot,
                torpedo_only=int(planet.id) in critical_ids,
            )
            if defensive_design is None:
                continue
            # Worlds which are economic hubs or nearer observed hostiles are
            # represented by territorial defense; the readiness gain provides
            # the local cost/time comparison.
            local_exposure_bonus=(
                1.25 + 0.10 * float(exposure.sustained_pressure)
                if int(planet.id) in exposed_world_ids else 0.0
            )
            upgrades.append((
                _planet_attack_pressure(state, planet, territorial) if int(planet.id) in critical_ids else 0,
                float(assessment["readiness_gain"]) + local_exposure_bonus,
                int(planet.population or 0),
                int(planet.id),
                planet,
                defensive_design,
                assessment,
            ))
        upgrades.sort(reverse=True)
        # Initial forts come first. Once every critical world has a shipyard,
        # upgrades go to the worlds enemies are actually attacking, with no
        # two-per-turn cap. Quiet worlds wait.
        if critical_ids and any(_lacks_shipyard_hub(planet) for planet in critical_planets):
            upgrade_rows = []
        elif critical_ids:
            attacked = [row for row in upgrades if row[0] > 0]
            upgrade_rows = attacked or upgrades[:1]
        else:
            upgrade_rows = upgrades[:2]
        for _attack, _gain, _population, _planet_id, planet, defensive_design, assessment in upgrade_rows:
            slot = int(defensive_design["design_number"])
            emergency = bool(territorial.needs_response or exposure.defensive_base_priority)
            queued_planets.add(int(planet.id))
            out.append(SupportBaseBuild(
                planet_id=int(planet.id),
                design_slot=slot,
                design_name=str(defensive_design.get("name") or defensive_design.get("hull_name") or f"Starbase #{slot + 1}"),
                hull_name=str(defensive_design.get("hull_name") or "Defensive starbase"),
                priority=220 if int(planet.id) in critical_ids else (198 if emergency else 186),
                reason=(
                    ("critical asset " if int(planet.id) in critical_ids else "")
                    + f"{'defensive' if emergency else 'current-tech'} starbase refit at {planet.name}: retain the installed base as orbital cover "
                    f"while building this armed replacement. Protection score "
                    f"{assessment['current_strength']:.1f}->{assessment['target_strength']:.1f}; "
                    f"estimated build time={assessment['build_turns']} turn(s) at "
                    f"{assessment['resources']} resources/turn, upgrade resource cost="
                    f"{assessment['resource_cost']} after crediting the installed starbase. "
                    + (
                        f"Selected for protection gained per construction turn; {territorial.reason}"
                        if emergency else
                        "A better station at a key world is cheaper than a new hull because the installed base is recycled."
                    )
                    + (
                        f" Alternate Reality habitat capacity "
                        f"{assessment['alternate_reality_habitat']['current_capacity']:,}->"
                        f"{assessment['alternate_reality_habitat']['target_capacity']:,} "
                        f"(+{assessment['alternate_reality_habitat']['added_capacity']:,}) is included as economic value."
                        if assessment.get("alternate_reality_habitat", {}).get("added_capacity", 0)
                        else ""
                    )
                ),
            ))

    # Key worlds get an armed station even after the cheap relay quota is met.
    # Two starts a turn keeps the bill from swallowing every yard.
    # Critical worlds wait for a torpedo station. A laser starter is not that station.
    torpedo_fortress = _torpedo_fortress_design(state) if critical_ids else None
    defensive_design = (
        torpedo_fortress
        if critical_ids else
        _preferred_support_design(state, prefer_defensive=True) or preferred
    )
    key_missing = [
        planet for planet in owned
        if int(planet.id) not in queued_planets
        and _planet_is_key_world(state, planet, network)
        and _lacks_shipyard_hub(planet)
        and (
            bool((planet.native or {}).get("is_homeworld"))
            or int(planet.population or 0) >= 40_000
        )
    ]
    if critical_ids:
        # The vital list is the build list. An older key-world rule must not
        # leave one of those planets out, and a short research wait must not
        # stop a world that still has no shipyard.
        key_missing = [
            planet for planet in owned
            if int(planet.id) in critical_ids
            and int(planet.id) not in queued_planets
            and _lacks_shipyard_hub(planet)
        ]
    key_missing.sort(key=lambda planet: (
        not bool((planet.native or {}).get("is_homeworld")),
        not world_faces_hostile(state, planet),
        -int(planet.population or 0),
        int(planet.id),
    ))
    key_hub_starts = 0
    # Every critical world starts its station once a torpedo design exists.
    # The old two-per-turn cap left the rest of the vital planets empty.
    # A laser starter does not count, so those worlds wait for the real station.
    new_hubs = [] if defensive_design is None and not critical_ids else (
        key_missing if critical_ids else key_missing[:MAX_NEW_SUPPORT_BASES_PER_TURN]
    )
    for planet in new_hubs:
        chosen_design = _fortress_design_for_planet(state, planet) if int(planet.id) in critical_ids else defensive_design
        if chosen_design is None:
            continue
        defensive_slot = int(chosen_design["design_number"])
        defensive_name = str(chosen_design.get("name") or chosen_design.get("hull_name") or f"Starbase #{defensive_slot + 1}")
        defensive_hull = str(chosen_design.get("hull_name") or "Defensive starbase")
        years = _profile_completion_years(state, planet, chosen_design) if int(planet.id) in critical_ids else None
        patience = _planet_fortress_patience(state, planet) if int(planet.id) in critical_ids else None
        queued_planets.add(int(planet.id))
        out.append(SupportBaseBuild(
            planet_id=int(planet.id),
            design_slot=defensive_slot,
            design_name=defensive_name,
            hull_name=defensive_hull,
            priority=220 if int(planet.id) in critical_ids else 210,
            reason=(
                (
                    f"critical asset at {planet.name}: invest this world's minerals in {defensive_name} ({defensive_hull}). "
                    + (
                        f"It finishes in about {years} year(s), inside this world's {patience}-year patience. "
                        if years is not None and patience is not None and years <= patience else
                        f"It is the armed station that finishes soonest, about {years} year(s). "
                        f"A larger hull does not fit this world's {patience}-year patience. "
                    )
                    if int(planet.id) in critical_ids else
                    f"defensive hub at key world {planet.name}: build {defensive_name} ({defensive_hull}) "
                )
                + "before ordinary ships. The station secures the world and covers the fleet that fights there. "
                + f"Support-base milestone={len(operational)} operational + {len(queued_planets)} queued / target {target}."
            ),
        ))
        key_hub_starts += 1

    deficit = max(0, int(target) - len(operational) - len(queued_planets))
    # A dense border crisis may need a new defensive hub even after the normal
    # economy-wide relay quota is met. Cap it at two starts so a response does
    # not turn into an uncontrolled starbase spending program.
    exposed_without_support=sum(
        1 for planet in owned
        if int(planet.id) in exposed_world_ids
        and not bool(((planet.native or {}).get("starbase_capabilities") or {}).get("can_refuel"))
    )
    if exposure.defensive_base_priority and exposed_without_support:
        deficit=max(deficit, min(MAX_NEW_SUPPORT_BASES_PER_TURN, exposed_without_support))
    # A distant Orbital Fort is not a shipyard/refuel hub.  Permit one upgrade
    # project even when the numerical support-base milestone is met, while the
    # material gate still prevents it from starving local development.
    remote_fort_needs_upgrade=any(
        bool(((p.native or {}).get("starbase_capabilities") or {}).get("is_orbital_fort"))
        and not bool(((p.native or {}).get("starbase_capabilities") or {}).get("can_refuel"))
        and distance_from_homeworld(state,p.position) >= 180.0
        for p in owned
    )
    if deficit <= 0 and remote_fort_needs_upgrade:
        deficit=1
    if deficit <= 0:
        return _queue_refuel_forts(state, out)

    candidates = []
    for planet in owned:
        if int(planet.id) in queued_planets:
            continue
        caps = (planet.native or {}).get("starbase_capabilities") or {}
        if bool(caps.get("can_refuel")) and bool(caps.get("can_build_ships")):
            continue
        has_base = bool((planet.native or {}).get("has_starbase", False))
        hub = next((h for h in network.hubs if int(h.planet_id) == int(planet.id)), None)
        # Designated Layer-1 hubs are intentionally based before they reach the
        # 25% graduation threshold so population and orbital development can
        # converge. Deeper children get a smaller early-base allowance.
        if hub is not None and hub.designated_layer1:
            minimum_population = 40_000 if has_base else 55_000
        elif hub is not None and hub.parent_exporter_id is not None:
            minimum_population = 50_000 if has_base else 70_000
        elif has_base:
            # An existing remote fort is already an orbital investment.  It is
            # a valid support-base *candidate* at a smaller population; the
            # separate material gate below/economy planner decides whether it
            # may occupy the production queue this turn.
            minimum_population = 20_000
        else:
            minimum_population = 45_000
        if int(planet.population or 0) < minimum_population:
            continue
        score, detail = _candidate_score(state, planet, operational, plan, network)
        if int(planet.id) in exposed_world_ids:
            score += 1.10 + 0.10 * float(exposure.sustained_pressure)
            detail["territorial_exposure"] = True
        else:
            detail["territorial_exposure"] = False
        # Below target we may build an inner economic hub too, but outer/frontier
        # worlds get the score needed to win naturally.
        if score < 1.20:
            continue
        candidates.append((score, planet, detail))

    candidates.sort(key=lambda row: row[0], reverse=True)
    starts = min(max(0, MAX_NEW_SUPPORT_BASES_PER_TURN - key_hub_starts), deficit, len(candidates))
    slot = int(preferred["design_number"])
    design_name = str(preferred.get("name") or preferred.get("hull_name") or f"Starbase #{slot + 1}")
    hull_name = str(preferred.get("hull_name") or "Support starbase")

    for score, planet, detail in candidates[:starts]:
        nearest_text = (
            "no operational support hub exists"
            if detail["nearest_hub"] >= 900
            else f"nearest support hub={detail['nearest_hub']:.1f} ly"
        )
        quiet_border = bool(allied_player_ids(state)) and not world_faces_hostile(state, planet)
        build_slot = slot
        build_name = design_name
        build_hull = hull_name
        if quiet_border:
            cheap = _cheapest_support_design(state)
            if cheap is not None:
                build_slot = int(cheap.get("design_number", slot) or slot)
                build_name = str(cheap.get("name") or cheap.get("hull_name") or design_name)
                build_hull = str(cheap.get("hull_name") or hull_name)
        out.append(SupportBaseBuild(
            planet_id=int(planet.id),
            design_slot=build_slot,
            design_name=build_name,
            hull_name=build_hull,
            priority=(
                132 if quiet_border else max(
                    152 if detail["layer1_pending"]
                else 147 if detail["next_layer_child"]
                else 145 if detail["outer"] or detail["network_gap"]
                else 138,
                    int(exposure.defensive_base_priority or 0)
                    if detail["territorial_exposure"] else 0,
                )
            ),
            reason=(
                (
                    f"cheap unarmed support base at allied-border world {planet.name}: "
                    f"{build_name} ({build_hull}) has the lowest bill because no hostile is nearby. "
                    if quiet_border else
                    f"promote high-value ring hub {planet.name} to {build_name} ({build_hull}): "
                )
                + ("restore remote refueling and ship-construction support; " if detail["is_fort"] else "")
                + f"{nearest_text}; ring={detail['ring']}; home distance={detail['home_distance']:.1f} ly; "
                f"nearby viable/unknown frontier={detail['viable']}/{detail['unknown']}; "
                f"population={int(planet.population or 0):,}; industry={detail['industry']}; "
                f"estimated resources={detail['resources']}; hub score={score:.2f}; "
                f"promotion=P{detail['promotion_tier']} rank={detail['promotion_rank']} "
                f"value={detail['promotion_value']:.2f}; layer1_pending={detail['layer1_pending']}; "
                f"next_layer_child={detail['next_layer_child']}; territorial_exposure="
                f"{detail['territorial_exposure']}. "
                f"Support-base milestone={len(operational)} operational + {len(queued_planets)} queued / target {target}."
            ),
        ))
    if not any("continue in-progress" not in build.reason for build in out):
        outward = _outward_fuel_world(state, owned, operational, queued_planets)
        if outward is not None:
            out.append(SupportBaseBuild(
                planet_id=int(outward.id),
                design_slot=slot,
                design_name=design_name,
                hull_name=hull_name,
                priority=146,
                complete_percent=0,
                reason=(
                    f"place one refuel base on the outward frontier at {outward.name}; "
                    f"population={int(outward.population or 0):,}. "
                    "The shared allied side does not need this base."
                ),
            ))
    return _queue_refuel_forts(state, out)


def _outward_fuel_world(state, owned, operational, queued_planets):
    """The far owned world that should receive the single open-side fuel base.

    Home and worlds that already refuel are skipped. Thirty thousand colonists
    is enough to start the base; the normal support-base gate stays higher.
    """

    if len(owned) < 2:
        return None
    allies = allied_player_ids(state)
    allied = [planet for planet in state.planets if planet.owner in allies]
    if allied:
        center_x = sum(planet.position.x for planet in allied) / len(allied)
        center_y = sum(planet.position.y for planet in allied) / len(allied)
    else:
        home = next((planet for planet in owned if bool((planet.native or {}).get("is_homeworld"))), owned[0])
        center_x = home.position.x
        center_y = home.position.y
    refuel_ids = {int(planet.id) for planet in operational}
    choices = []
    for planet in owned:
        if int(planet.id) in queued_planets or int(planet.id) in refuel_ids:
            continue
        if bool(((planet.native or {}).get("starbase_capabilities") or {}).get("can_refuel")):
            continue
        if int(planet.population or 0) < 30_000:
            continue
        span = math.hypot(planet.position.x - center_x, planet.position.y - center_y)
        if span < 70.0:
            continue
        choices.append((span, int(planet.id), planet))
    if not choices:
        return None
    choices.sort(reverse=True)
    return choices[0][2]


@lru_cache(maxsize=1)
def _stock_starbase_hull_costs() -> dict[int, dict[str, int]]:
    """Read stock hull costs from the same MOD data used for native designs."""
    path = native_resource_path("mod", "data_hulls.mod")
    costs: dict[int, dict[str, int]] = {}
    for row in csv.reader(path.read_text(encoding="latin-1").splitlines()):
        if not row or int(row[0]) != 16:
            continue
        numbers = [int(value) if value else 0 for value in row[3:]]
        if len(numbers) < 12:
            continue
        hull_id = int(numbers[0])
        costs[hull_id] = {
            "resources": int(numbers[8]),
            "ironium": int(numbers[9]),
            "boranium": int(numbers[10]),
            "germanium": int(numbers[11]),
        }
    return costs


def _raw_slot_value(slot, key: str, default: int = 0) -> int:
    if isinstance(slot, dict):
        value = slot.get(key, default)
    else:
        value = getattr(slot, key, default)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return int(default)


def recycled_starbase_cost(new_profile: dict, installed_profile: dict | None) -> dict[str, int]:
    """Bill only the difference. Stars! credits the starbase already in orbit.

    Resources and each mineral are reduced by what the installed base already
    cost, and never go below zero. A world with no base pays the full design.
    """

    gross = support_base_design_cost(new_profile)
    if not installed_profile:
        return gross
    credited = support_base_design_cost(installed_profile)
    return {
        key: max(0, int(gross.get(key, 0) or 0) - int(credited.get(key, 0) or 0))
        for key in ("resources", "ironium", "boranium", "germanium")
    }


def _installed_starbase_profile(state, planet) -> dict | None:
    native = getattr(planet, "native", {}) or {}
    if not bool(native.get("has_starbase", native.get("starbase", False))):
        return None
    slot = native.get("starbase_design")
    if slot is None:
        hull_id = int((native.get("starbase_capabilities") or {}).get("hull_id", -1) or -1)
        if hull_id < 0:
            return None
        return {"hull_id": hull_id, "slots": [], "design_number": -1}
    slot = int(slot)
    for profile in (state.native or {}).get("starbase_profiles", []) or []:
        if int(profile.get("design_number", -1)) == slot:
            return profile
    hull_id = int((native.get("starbase_capabilities") or {}).get("hull_id", -1) or -1)
    return {"hull_id": hull_id, "slots": [], "design_number": slot} if hull_id >= 0 else None


def support_base_design_cost(profile: dict) -> dict[str, int]:
    """Return the exact stock hull + fitted-component bill where slots exist.

    Old/hand-built test states may not carry slot data.  In that case the bare
    hull bill is still a safe lower bound, so the planner never invents a free
    Space Station.
    """
    hull_id = int(profile.get("hull_id", -1) or -1)
    total = dict(_stock_starbase_hull_costs().get(hull_id, {
        "resources": 0, "ironium": 0, "boranium": 0, "germanium": 0,
    }))
    slots = list(profile.get("slots", []) or [])
    database = stock_component_database()
    for slot in slots:
        category = _raw_slot_value(slot, "category")
        item_id = _raw_slot_value(slot, "item_id")
        count = max(0, _raw_slot_value(slot, "count"))
        component = database.component(category, item_id)
        if component is None or count <= 0:
            continue
        total["resources"] += count * int(component.resource_cost)
        total["ironium"] += count * int(component.ironium)
        total["boranium"] += count * int(component.boranium)
        total["germanium"] += count * int(component.germanium)
    return total


def plan_support_base_material_demands(state, plan=None) -> list[SupportBaseMaterialDemand]:
    """Expose ready/blocked base work for production and transport decisions."""
    requests = plan_support_base_builds(state, plan)
    profiles = _support_designs(state)
    economy = decode_race_economy(state.race)
    planets = {int(p.id): p for p in state.planets if p.owner == state.player_id}
    out: list[SupportBaseMaterialDemand] = []
    for request in requests:
        planet = planets.get(int(request.planet_id))
        profile = profiles.get(int(request.design_slot))
        if planet is None or profile is None:
            continue
        installed = _installed_starbase_profile(state, planet)
        same_design = (
            installed is not None
            and int(installed.get("design_number", -2)) == int(profile.get("design_number", -1))
        )
        cost = (
            support_base_design_cost(profile)
            if installed is None or same_design
            else recycled_starbase_cost(profile, installed)
        )
        completion = min(100, max(0, int(request.complete_percent or 0)))
        # The native queue reports whole-percent completion.  Round remaining
        # material up so a 99% record cannot be treated as already funded.
        remaining_fraction = max(0.0, (100 - completion) / 100.0)
        mineral_remaining = {
            mineral: int(math.ceil(int(cost[mineral]) * remaining_fraction))
            for mineral in ("ironium", "boranium", "germanium")
        }
        working = working_mineral_reserve(planet, economy, queue=())
        mineral_required = {
            mineral: mineral_remaining[mineral] + int(working[mineral])
            for mineral in mineral_remaining
        }
        stock = mineral_surface_stock(planet)
        deficit = {
            mineral: max(0, mineral_required[mineral] - int(stock[mineral]))
            for mineral in mineral_required
        }
        ready = not any(deficit.values())
        outlook = ""
        if bool((planet.native or {}).get("critical_asset")) and not ready:
            from .critical_assets import FORTRESS_BUILD_SPAN, fortress_construction_outlook

            window = fortress_construction_outlook(
                state, planet, deficit, int(math.ceil(int(cost["resources"]) * remaining_fraction)),
            )
            donors = ", ".join(window["import_from"]) or "no donor in range"
            mineral = window["mineral_years"]
            resources = window["resource_years"]
            span = window["window_years"]
            mineral_text = "undated" if mineral is None else f"{mineral} year(s)"
            resource_text = "undated" if resources is None else f"{resources} year(s)"
            if span is None:
                timing = (
                    f" Resources are {resource_text} and imported minerals are {mineral_text}, "
                    "so the construction window cannot be dated yet."
                )
            elif span <= FORTRESS_BUILD_SPAN:
                timing = (
                    f" Resources take {resource_text} and imported minerals take {mineral_text}. "
                    f"The construction window is {span} year(s)."
                )
            else:
                timing = (
                    f" Resources take {resource_text} and imported minerals take {mineral_text}. "
                    f"That is {span} years, past the {FORTRESS_BUILD_SPAN}-year fortification span."
                )
            outlook = (
                timing
                + f" Import from {donors}. Do not queue more mines on {planet.name}."
            )
        shortage = "/".join(
            f"{mineral[0].upper()}={deficit[mineral]}"
            for mineral in ("ironium", "boranium", "germanium") if deficit[mineral]
        ) or "none"
        out.append(SupportBaseMaterialDemand(
            planet_id=int(planet.id),
            design_slot=int(request.design_slot),
            design_name=str(request.design_name),
            complete_percent=completion,
            resource_remaining=int(math.ceil(int(cost["resources"]) * remaining_fraction)),
            mineral_remaining=mineral_remaining,
            mineral_required=mineral_required,
            mineral_deficit=deficit,
            ready=ready,
            reason=(
                f"{request.design_name} material gate at {planet.name}: remaining I/B/G="
                f"{mineral_remaining['ironium']}/{mineral_remaining['boranium']}/{mineral_remaining['germanium']} kT; "
                f"working-stock target I/B/G={mineral_required['ironium']}/{mineral_required['boranium']}/{mineral_required['germanium']} kT; "
                f"shortage {shortage}. "
                + ("Queue may proceed." if ready else "Hold the base queue and route minerals before construction resumes.")
                + outlook
            ),
        ))
    return out
