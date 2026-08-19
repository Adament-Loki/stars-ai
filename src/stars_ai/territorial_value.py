
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable


@dataclass
class PlanetInvestment:
    """
    Normalized investment/value estimate for one owned planet.

    All component scores are 0..1. `total_value` is also clamped to 0..1.
    """
    population_value: float
    infrastructure_value: float
    mineral_value: float
    strategic_value: float
    core_proximity_value: float
    logistics_value: float
    irreplaceability_value: float
    total_value: float


@dataclass
class PlanetDefensePosture:
    """
    Recommended reaction to pressure or attack on a planet.
    """
    investment: PlanetInvestment
    abandonability: float
    defense_priority: float
    escalation_priority: float
    recommended_response: str
    reason: str


def _xy(obj: Any) -> tuple[float, float] | None:
    x = getattr(obj, "x", None)
    y = getattr(obj, "y", None)
    if x is None or y is None:
        return None
    return float(x), float(y)


def _dist(a: Any, b: Any) -> float | None:
    pa, pb = _xy(a), _xy(b)
    if pa is None or pb is None:
        return None
    return math.hypot(pa[0] - pb[0], pa[1] - pb[1])


def _owned_planets(state: Any) -> list[Any]:
    pid = getattr(state, "player_id", None)
    out = []
    for p in getattr(state, "planets", []) or []:
        owner = getattr(p, "owner_id", getattr(p, "owner", None))
        if owner == pid:
            out.append(p)
    return out


def _homeworlds(state: Any) -> list[Any]:
    return [p for p in _owned_planets(state) if bool(getattr(p, "is_homeworld", getattr(p, "homeworld", False)))]


def _estimate_core_planets(state: Any) -> list[Any]:
    """
    Prefer explicit homeworld(s). If unavailable, infer the core from the most
    populous / developed owned planets.
    """
    hw = _homeworlds(state)
    if hw:
        return hw

    owned = _owned_planets(state)
    if not owned:
        return []

    def score(p: Any) -> float:
        pop = float(getattr(p, "population", 0) or 0)
        fac = float(getattr(p, "factories", 0) or 0)
        mines = float(getattr(p, "mines", 0) or 0)
        sb = 1.0 if getattr(p, "has_starbase", False) else 0.0
        return pop + 250.0 * fac + 100.0 * mines + 20000.0 * sb

    return sorted(owned, key=score, reverse=True)[: max(1, min(3, len(owned)))]


def _norm_sat(value: float, scale: float) -> float:
    if value <= 0:
        return 0.0
    # Smooth saturation avoids a single huge world dominating.
    return max(0.0, min(1.0, value / (value + scale)))


def _population_value(p: Any) -> float:
    pop = float(getattr(p, "population", 0) or 0)
    return _norm_sat(pop, 50000.0)


def _infrastructure_value(p: Any) -> float:
    factories = float(getattr(p, "factories", 0) or 0)
    mines = float(getattr(p, "mines", 0) or 0)
    defenses = float(getattr(p, "defenses", 0) or 0)
    starbase = 1.0 if getattr(p, "has_starbase", False) else 0.0
    scanner = 1.0 if getattr(p, "has_scanner", False) else 0.0

    raw = (
        0.48 * _norm_sat(factories, 120.0)
        + 0.27 * _norm_sat(mines, 120.0)
        + 0.12 * _norm_sat(defenses, 60.0)
        + 0.10 * starbase
        + 0.03 * scanner
    )
    return max(0.0, min(1.0, raw))


def _mineral_value(p: Any) -> float:
    # Surface stock first.
    vals = []
    for attr in ("ironium", "boranium", "germanium"):
        v = getattr(p, attr, None)
        if v is not None:
            vals.append(_norm_sat(float(v), 25000.0))
    stock = sum(vals) / len(vals) if vals else None

    concs = []
    for attr in ("ironium_conc", "boranium_conc", "germanium_conc"):
        v = getattr(p, attr, None)
        if v is not None:
            concs.append(max(0.0, min(1.0, float(v) / 100.0)))
    conc = sum(concs) / len(concs) if concs else None

    if stock is not None and conc is not None:
        return 0.65 * stock + 0.35 * conc
    if stock is not None:
        return stock
    if conc is not None:
        return conc
    return 0.35


def _core_proximity_value(state: Any, p: Any) -> float:
    cores = _estimate_core_planets(state)
    if not cores:
        return 0.5
    dists = [d for c in cores if (d := _dist(c, p)) is not None]
    if not dists:
        return 0.5
    d = min(dists)

    # Approximate strategic geography. ~0 LY = core; ~250+ LY = very peripheral.
    return max(0.0, min(1.0, 1.0 - d / 250.0))


def _local_network_value(state: Any, p: Any) -> float:
    """
    A remote isolated colony is less valuable than a planet embedded in the
    owned logistics network, even with similar local installations.
    """
    owned = [q for q in _owned_planets(state) if q is not p]
    if not owned:
        return 0.25

    near = 0.0
    for q in owned:
        d = _dist(p, q)
        if d is None:
            continue
        if d <= 50:
            near += 1.0
        elif d <= 100:
            near += 0.5
        elif d <= 150:
            near += 0.2

    return max(0.0, min(1.0, near / 4.0))


def _strategic_value(state: Any, p: Any) -> float:
    explicit = getattr(p, "strategic_value", None)
    if explicit is not None:
        return max(0.0, min(1.0, float(explicit)))

    v = 0.0
    if getattr(p, "is_homeworld", getattr(p, "homeworld", False)):
        v += 0.60
    if getattr(p, "has_starbase", False):
        v += 0.18
    if getattr(p, "has_scanner", False):
        v += 0.06
    if getattr(p, "has_route", False) or getattr(p, "route", None) not in (None, -1, 0):
        v += 0.05

    # Frontier planets are strategically useful, but strategic location alone
    # should not make a tiny remote colony existentially important.
    frontier = getattr(p, "frontier_value", None)
    if frontier is not None:
        v += 0.15 * max(0.0, min(1.0, float(frontier)))

    return max(0.0, min(1.0, v))


def _irreplaceability(state: Any, p: Any) -> float:
    """
    Captures sunk cost and uniqueness beyond current production output.
    A homeworld or singular high-population hub is hard to replace; a fresh
    remote colony is not.
    """
    if getattr(p, "is_homeworld", getattr(p, "homeworld", False)):
        return 1.0

    owned = _owned_planets(state)
    if not owned:
        return 0.0

    pop = float(getattr(p, "population", 0) or 0)
    pops = sorted((float(getattr(q, "population", 0) or 0) for q in owned), reverse=True)
    if pops and pop >= pops[max(0, min(len(pops)-1, 2))] and pop > 0:
        return 0.70

    if getattr(p, "has_starbase", False):
        return 0.55

    return 0.15


def estimate_planet_investment(state: Any, planet: Any) -> PlanetInvestment:
    pop = _population_value(planet)
    infra = _infrastructure_value(planet)
    minerals = _mineral_value(planet)
    strategic = _strategic_value(state, planet)
    core = _core_proximity_value(state, planet)
    logistics = _local_network_value(state, planet)
    irrep = _irreplaceability(state, planet)

    # Sunk cost and species risk emphasize population + infrastructure + core.
    total = (
        0.27 * pop
        + 0.22 * infra
        + 0.10 * minerals
        + 0.14 * strategic
        + 0.13 * core
        + 0.06 * logistics
        + 0.08 * irrep
    )
    total = max(0.0, min(1.0, total))

    return PlanetInvestment(
        population_value=pop,
        infrastructure_value=infra,
        mineral_value=minerals,
        strategic_value=strategic,
        core_proximity_value=core,
        logistics_value=logistics,
        irreplaceability_value=irrep,
        total_value=total,
    )


def assess_defense_posture(
    state: Any,
    planet: Any,
    *,
    attack_strength: float = 0.5,
    attacker_hostility: float = 0.5,
) -> PlanetDefensePosture:
    inv = estimate_planet_investment(state, planet)

    threat = max(0.0, min(1.0, 0.65 * attack_strength + 0.35 * attacker_hostility))

    defense_priority = max(
        0.0,
        min(1.0, inv.total_value * (0.70 + 0.60 * threat))
    )

    # Abandonability is deliberately high for low-investment peripheral worlds.
    abandonability = max(
        0.0,
        min(
            1.0,
            (1.0 - inv.total_value)
            * (1.05 - 0.45 * inv.core_proximity_value)
            * (1.0 - 0.40 * inv.irreplaceability_value)
        )
    )

    # Escalation is about whether the attack justifies broader hostility/war.
    # Core/high-pop worlds cause much more escalation than expendable colonies.
    escalation = max(
        0.0,
        min(
            1.0,
            threat
            * (
                0.35 * inv.population_value
                + 0.25 * inv.infrastructure_value
                + 0.20 * inv.core_proximity_value
                + 0.20 * inv.irreplaceability_value
            )
        )
    )

    if defense_priority >= 0.78 or escalation >= 0.70:
        response = "DEFEND_AT_HIGH_PRIORITY"
        reason = "High sunk cost / core-species risk makes loss strategically unacceptable."
    elif defense_priority >= 0.52:
        response = "DEFEND_IF_COST_EFFECTIVE"
        reason = "Planet has meaningful value, but defense should still be weighed against fleet/economic cost."
    elif abandonability >= 0.68:
        response = "LIMITED_RESPONSE_OR_ABANDON"
        reason = "Remote/low-investment world is not worth a disproportionate war or fleet commitment."
    else:
        response = "CONTAIN_AND_MONITOR"
        reason = "Moderate strategic value: avoid escalation unless the attack becomes part of a broader threat."

    return PlanetDefensePosture(
        investment=inv,
        abandonability=abandonability,
        defense_priority=defense_priority,
        escalation_priority=escalation,
        recommended_response=response,
        reason=reason,
    )


def territorial_loss_penalty(state: Any, planets: Iterable[Any]) -> float:
    """
    Aggregate 0..1 penalty useful in conflict risk/reward calculations.
    Losing several low-value colonies can still add up, while losing a core
    world immediately produces a large penalty.
    """
    vals = sorted(
        (estimate_planet_investment(state, p).total_value for p in planets),
        reverse=True
    )
    if not vals:
        return 0.0

    remaining = 1.0
    for v in vals:
        remaining *= (1.0 - 0.75 * v)
    return max(0.0, min(1.0, 1.0 - remaining))
