
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from .util import distance
from .population_units import COLONISTS_PER_CARGO_KT, COLONY_LOAD_COLONISTS

@dataclass
class PlanetEconomicRole:
    planet_id: int
    role: str
    target_capacity_fraction: float
    score: float
    reason: str

@dataclass
class PopulationTransferPlan:
    source_planet_id: int
    destination_planet_id: int
    population: int
    fleet_id: int | None
    score: float
    reason: str

def classify_planet_role(planet: Any) -> PlanetEconomicRole:
    hab = float(getattr(planet, "habitability", 0) or 0)
    pop = int(getattr(planet, "population", 0) or 0)
    factories = int(getattr(planet, "factories", 0) or 0)
    native = getattr(planet, "native", {}) or {}
    capacity = int(native.get("capacity_population", max(pop, 1000000)) or max(pop,1000000))
    frac = pop / max(1, capacity)

    if hab >= 70 and frac <= 0.35:
        return PlanetEconomicRole(planet.id, "BREEDER", 0.25, hab/100, "High-habitability uncrowded world; maximize compounding population growth.")
    if pop < 100000 and hab >= 40:
        return PlanetEconomicRole(planet.id, "DEVELOPING", 0.50, 0.7, "Young colony benefits strongly from imported population and infrastructure.")
    if factories > 0 and frac < 0.65:
        return PlanetEconomicRole(planet.id, "INDUSTRIAL", 0.60, 0.65, "Existing infrastructure rewards maintaining enough population to operate it.")
    if frac >= 0.50 and hab >= 50:
        return PlanetEconomicRole(planet.id, "EXPORTER", 0.50, 0.8, "Mature good world can export excess population without sacrificing core productivity.")
    return PlanetEconomicRole(planet.id, "MATURE", 0.60, 0.5, "Stable world; balance local resources and empire-wide growth.")

def optimize_population_transfers(state: Any, max_transfers: int = 8) -> list[PopulationTransferPlan]:
    owned = [p for p in state.planets if p.owner == state.player_id]
    roles = {p.id: classify_planet_role(p) for p in owned}
    donors = [
        p for p in owned
        if p.population >= 150000
        and roles[p.id].role not in ("DEVELOPING",)
    ]
    receivers = [p for p in owned if roles[p.id].role in ("DEVELOPING","BREEDER") and (p.habitability or 0) >= 30]
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
            f"Move population from {src.name} to higher-growth/developing {dst.name}; balances breeder growth, travel, and strategic value."))
        used_dst.add(dst.id)
        if fleet in freighters: freighters.remove(fleet)
    return plans
