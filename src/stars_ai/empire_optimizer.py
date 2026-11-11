
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from .diplomacy import allied_player_ids
from .util import distance
from .population_units import COLONISTS_PER_CARGO_KT, COLONY_LOAD_COLONISTS

# Location is distance to the nearest hostile fleet or observed hostile colony.
# A world inside contact range is risky. One farther out, or sitting on an
# unowned neighbor, is moderate. Everything else is safe.
RISKY_ZONE_LY = 90.0
MODERATE_ZONE_LY = 180.0
FRONTIER_ZONE_LY = 70.0

# Ironium builds ships and defenses. Germanium builds factories and gates.
# Boranium is the third concentration and is not given that premium.
IRONIUM_WEIGHT = 1.3
BORANIUM_WEIGHT = 1.0
GERMANIUM_WEIGHT = 1.5
_MINERAL_WEIGHT_SUM = IRONIUM_WEIGHT + BORANIUM_WEIGHT + GERMANIUM_WEIGHT

HIGH_HABITABILITY = 70
AVERAGE_HABITABILITY = 30
HIGH_MINERAL_SCORE = 55.0
AVERAGE_MINERAL_SCORE = 30.0
# Share of the world's population-plus-factory resource ceiling.
# A normal race splits that ceiling about evenly, so population alone cannot
# cross into Developed. Factories have to be built too.
SOLID_POP_SCORE = 20.0
DEVELOPED_SCORE = 55.0

@dataclass
class PlanetEconomicRole:
    planet_id: int
    role: str
    target_capacity_fraction: float
    score: float
    reason: str
    zone: str = "MODERATE"
    habit_band: str = "UNKNOWN"
    mineral_band: str = "UNKNOWN"
    mineral_score: float = 0.0
    development_band: str = "COLONY"
    development_score: float = 0.0

@dataclass
class PopulationTransferPlan:
    source_planet_id: int
    destination_planet_id: int
    population: int
    fleet_id: int | None
    score: float
    reason: str

def _concentrations(planet: Any) -> tuple[float, float, float] | None:
    raw = (getattr(planet, "native", {}) or {}).get("mineral_concentrations")
    if not isinstance(raw, (list, tuple)) or len(raw) < 3:
        return None
    try:
        return (float(raw[0]), float(raw[1]), float(raw[2]))
    except (TypeError, ValueError):
        return None


def mineral_value(planet: Any) -> tuple[str, float]:
    """Weighted mineral score from concentration and, when known, surface stock.

    Ironium and germanium pull the score up more than boranium. Surface stock
    is used when this world is owned or any stock has actually been seen.
    A scanned empty unowned world stays on concentration alone, because a
    zero stock there usually means the surface was not read.
    """

    concentrations = _concentrations(planet)
    if concentrations is None:
        return "UNKNOWN", 0.0
    conc_score = (
        IRONIUM_WEIGHT * concentrations[0]
        + BORANIUM_WEIGHT * concentrations[1]
        + GERMANIUM_WEIGHT * concentrations[2]
    ) / _MINERAL_WEIGHT_SUM
    stocks = (
        int(getattr(planet, "ironium", 0) or 0),
        int(getattr(planet, "boranium", 0) or 0),
        int(getattr(planet, "germanium", 0) or 0),
    )
    surface_known = getattr(planet, "owner", None) is not None or any(stock > 0 for stock in stocks)
    if surface_known:
        weighted_stock = (
            IRONIUM_WEIGHT * max(0, stocks[0])
            + BORANIUM_WEIGHT * max(0, stocks[1])
            + GERMANIUM_WEIGHT * max(0, stocks[2])
        )
        surface_score = min(100.0, weighted_stock / 50.0)
        score = min(100.0, 0.75 * conc_score + 0.25 * surface_score)
    else:
        score = min(100.0, conc_score)
    if score >= HIGH_MINERAL_SCORE:
        band = "HIGH"
    elif score >= AVERAGE_MINERAL_SCORE:
        band = "AVERAGE"
    else:
        band = "LOW"
    return band, round(score, 1)


def location_zone(planet: Any, state: Any | None) -> str:
    """Safe, moderate, or risky from hostile distance and the local frontier."""

    if state is None or getattr(planet, "position", None) is None:
        return "MODERATE"
    me = int(getattr(state, "player_id", 0) or 0)
    allies = allied_player_ids(state)
    nearest_hostile: float | None = None
    nearest_unowned: float | None = None

    def consider_hostile(position: Any) -> None:
        nonlocal nearest_hostile
        gap = float(distance(position, planet.position))
        nearest_hostile = gap if nearest_hostile is None else min(nearest_hostile, gap)

    for fleet in getattr(state, "fleets", []) or []:
        owner = getattr(fleet, "owner", None)
        if owner is None or int(owner) == me or int(owner) in allies:
            continue
        if getattr(fleet, "position", None) is None:
            continue
        consider_hostile(fleet.position)
    for other in getattr(state, "planets", []) or []:
        if getattr(other, "id", None) == getattr(planet, "id", None):
            continue
        if getattr(other, "position", None) is None:
            continue
        owner = getattr(other, "owner", None)
        if owner is not None and (int(owner) == me or int(owner) in allies):
            continue
        if owner is not None:
            if not bool(getattr(other, "observed", False)):
                continue
            consider_hostile(other.position)
            continue
        gap = float(distance(other.position, planet.position))
        nearest_unowned = gap if nearest_unowned is None else min(nearest_unowned, gap)

    if nearest_hostile is not None and nearest_hostile <= RISKY_ZONE_LY:
        return "RISKY"
    if nearest_hostile is not None and nearest_hostile <= MODERATE_ZONE_LY:
        return "MODERATE"
    if nearest_unowned is not None and nearest_unowned <= FRONTIER_ZONE_LY:
        return "MODERATE"
    return "SAFE"


def _eventual_habitability(planet: Any, state: Any | None) -> int | None:
    if state is None:
        return None
    from .terraforming import evaluate_terraforming

    eventual = evaluate_terraforming(state, planet).eventual_habitability
    return None if eventual is None else int(eventual)


def habit_band(planet: Any, state: Any | None = None) -> str:
    """High is 70% or more. Average is 30% or more. Low is liveable below that.

    A negative world stays low when terraforming can bring it to 0 or better.
    A red world that stays red even at the race's terraforming ceiling is
    unliveable.
    """

    raw = getattr(planet, "habitability", None)
    if raw is None:
        return "UNKNOWN"
    hab = int(raw)
    if hab >= HIGH_HABITABILITY:
        return "HIGH"
    if hab >= AVERAGE_HABITABILITY:
        return "AVERAGE"
    if hab >= 0:
        return "LOW"
    eventual = _eventual_habitability(planet, state)
    if eventual is None:
        return "UNKNOWN"
    if eventual >= 0:
        return "LOW"
    return "UNLIVEABLE"


def development_value(planet: Any, state: Any | None) -> tuple[str, float]:
    """How built the world's resource base is: Colony, Solid Pop, or Developed.

    The score is the share of the resources this world would produce at
    population capacity with every factory that population can operate.
    Current resources are the population term plus the factories that are
    actually built and operable. A full population with no factories stays
    Solid Pop on a normal race, because factories are about half the ceiling.
    """

    from .planet_economy import (
        decode_race_economy,
        estimated_operating_resources,
        installation_caps,
        planet_population_capacity,
    )

    race = getattr(state, "race", None) if state is not None else None
    economy = decode_race_economy(race)
    energy = 0
    if state is not None:
        energy = int(getattr(getattr(state, "tech", None), "energy", 0) or 0)
    current = int(estimated_operating_resources(planet, economy, energy_tech=energy)["estimated_resources"])
    capacity = planet_population_capacity(planet, race)
    factory_cap, _mines = installation_caps(capacity, economy)
    if economy.alternate_reality:
        ceiling = (capacity // max(1, economy.colonists_per_resource)) * (energy + 1) // 10
    else:
        ceiling = (
            capacity // max(1, economy.colonists_per_resource)
            + (factory_cap * economy.factory_output_per_10) // 10
        )
    score = 0.0 if ceiling <= 0 else min(100.0, 100.0 * current / ceiling)
    if score >= DEVELOPED_SCORE:
        band = "DEVELOPED"
    elif score >= SOLID_POP_SCORE:
        band = "SOLID_POP"
    else:
        band = "COLONY"
    return band, round(score, 1)


def _strategy(zone: str, habit: str, mineral: str) -> tuple[str, float]:
    """One durable job from zone, habit, and minerals.

    Development is a separate axis. A safe high-habitability world grows
    resources whether it is still a colony or already developed. The same
    world in a risky zone is industrialized and fortified.
    """

    if habit == "UNLIVEABLE":
        return "SKIP", 0.0
    if habit == "UNKNOWN":
        if mineral == "HIGH":
            return ("FORTIFY", 0.75) if zone == "RISKY" else ("INDUSTRIAL", 0.60)
        return "OUTPOST", 0.20
    if zone == "RISKY":
        if habit == "HIGH" or (habit == "AVERAGE" and mineral != "LOW") or mineral == "HIGH":
            return "FORTIFY", 0.75
        return "OUTPOST", 0.20
    if habit == "HIGH":
        return "GROWTH", 0.65
    if habit == "AVERAGE" or mineral == "HIGH" or (mineral == "AVERAGE" and zone == "SAFE"):
        return "INDUSTRIAL", 0.60
    return "OUTPOST", 0.20


def classify_planet_role(planet: Any, state: Any | None = None) -> PlanetEconomicRole:
    zone = location_zone(planet, state)
    habit = habit_band(planet, state)
    mineral, mineral_score = mineral_value(planet)
    development, development_score = development_value(planet, state)
    role, target = _strategy(zone, habit, mineral)
    hab = getattr(planet, "habitability", None)
    hab_score = 0.0 if hab is None else max(0.0, min(1.0, (float(hab) + 15.0) / 115.0))
    score = round(0.65 * hab_score + 0.35 * (mineral_score / 100.0), 3)
    development_label = {"COLONY": "colony", "SOLID_POP": "solid pop", "DEVELOPED": "developed"}[development]
    reason = (
        f"{development_label} ({development_score:.0f}% of population and factory resources), "
        f"{zone.lower()} zone, {habit.lower()} habitability, {mineral.lower()} minerals "
        f"(ironium x{IRONIUM_WEIGHT:g}, germanium x{GERMANIUM_WEIGHT:g}, score {mineral_score:.0f}): "
        f"{role.lower()}."
    )
    return PlanetEconomicRole(
        planet.id, role, target, score, reason,
        zone=zone, habit_band=habit, mineral_band=mineral, mineral_score=mineral_score,
        development_band=development, development_score=development_score,
    )

def optimize_population_transfers(state: Any, max_transfers: int = 8) -> list[PopulationTransferPlan]:
    owned = [p for p in state.planets if p.owner == state.player_id]
    roles = {p.id: classify_planet_role(p, state) for p in owned}
    donors = [
        p for p in owned
        if roles[p.id].development_band in ("SOLID_POP", "DEVELOPED")
        and roles[p.id].role not in ("SKIP", "FORTIFY")
    ]
    receivers = [
        p for p in owned
        if roles[p.id].development_band == "COLONY"
        and roles[p.id].role in ("GROWTH", "FORTIFY", "INDUSTRIAL")
        and (p.habitability or 0) >= 30
    ]
    freighters = [f for f in state.fleets if f.owner == state.player_id and f.role == "freighter" and f.cargo_capacity > 0]

    candidates = []
    for src in donors:
        for dst in receivers:
            if src.id == dst.id: continue
            hab_gain = max(0.0, (dst.habitability or 0) / 100.0)
            d = distance(src.position, dst.position)
            strategic = float((dst.native or {}).get("strategic_value",0.5))
            score = 1.4*hab_gain + 0.6*strategic - d/500.0
            candidates.append((score,src,dst))
    candidates.sort(reverse=True, key=lambda x:x[0])

    plans=[]
    used_dst=set()
    for score,src,dst in candidates:
        if len(plans)>=max_transfers: break
        if dst.id in used_dst: continue
        fleet = min(freighters, key=lambda f: distance(f.position,src.position), default=None)
        cap = (
            int(fleet.cargo_capacity) * COLONISTS_PER_CARGO_KT
            if fleet else COLONY_LOAD_COLONISTS
        )
        amount = min(cap, max(10000, int(src.population*0.10)))
        plans.append(PopulationTransferPlan(src.id,dst.id,amount,getattr(fleet,"id",None),score,
            f"Move population from {src.name} to {dst.name} ({roles[dst.id].role}, {roles[dst.id].zone} zone)."))
        used_dst.add(dst.id)
        if fleet in freighters: freighters.remove(fleet)
    return plans
