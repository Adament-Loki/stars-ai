
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


class StrategicPhase(str, Enum):
    EARLY_EXPANSION = "early_expansion"
    FRONTIER_CONTACT = "frontier_contact"
    CONSOLIDATION = "consolidation"
    OPPORTUNISTIC_WAR = "opportunistic_war"
    FORTIFICATION = "fortification"
    TECH_ACCELERATION = "tech_acceleration"
    INDUSTRIAL_BUILDOUT = "industrial_buildout"


@dataclass
class PlanetPreference:
    """
    Controls how selective a persona is about worlds during expansion.

    min_habitability:
        Minimum acceptable habitability when known. None means do not enforce.

    min_resource_score:
        Minimum normalized resource score (0..1) when enough mineral information
        is known. None means do not enforce.

    selectivity:
        0 = colonize almost anything legal/reachable
        1 = only take very strong worlds unless strategically necessary

    frontier_exception:
        Allows strategically useful border/bridge worlds to be selected even if
        they fall below normal economic thresholds.
    """
    min_habitability: float | None = None
    min_resource_score: float | None = None
    selectivity: float = 0.35
    frontier_exception: bool = True


@dataclass
class PhasePolicy:
    phase: StrategicPhase
    explore_weight: float
    expand_weight: float
    develop_weight: float
    fortify_weight: float
    attack_weight: float
    research_weight: float
    notes: list[str] = field(default_factory=list)


@dataclass
class FrontierAssessment:
    explored_fraction: float
    owned_planets: int
    known_enemy_players: int
    contested_frontier_fraction: float
    open_frontier_fraction: float
    frontier_pressure: float
    expansion_saturation: float

    @property
    def has_contact(self) -> bool:
        return self.known_enemy_players > 0 or self.contested_frontier_fraction > 0.0


@dataclass
class PhaseDecision:
    phase: StrategicPhase
    assessment: FrontierAssessment
    policy: PhasePolicy
    reason: str


def _owned_planets(state: Any) -> list[Any]:
    player_id = getattr(state, "player_id", None)
    planets = getattr(state, "planets", []) or []
    return [p for p in planets if getattr(p, "owner_id", getattr(p, "owner", None)) == player_id]


def _known_planets(state: Any) -> list[Any]:
    return list(getattr(state, "planets", []) or [])


def _known_other_players(state: Any) -> set[int]:
    player_id = getattr(state, "player_id", None)
    players = set()
    for fleet in getattr(state, "fleets", []) or []:
        owner = getattr(fleet, "owner_id", getattr(fleet, "owner", None))
        if owner is not None and owner != player_id:
            players.add(int(owner))
    for planet in getattr(state, "planets", []) or []:
        owner = getattr(planet, "owner_id", getattr(planet, "owner", None))
        if owner is not None and owner not in (-1, player_id):
            players.add(int(owner))
    return players


def _planet_is_observed(p: Any) -> bool:
    if hasattr(p, "observed"):
        return bool(getattr(p, "observed"))
    # Native bridge uses map-known worlds with sparse/None attributes.
    for attr in ("gravity", "temperature", "radiation", "population", "owner_id", "owner"):
        if getattr(p, attr, None) not in (None, -1):
            return True
    return False


def assess_frontier(state: Any) -> FrontierAssessment:
    planets = _known_planets(state)
    total = max(1, len(planets))
    observed = sum(1 for p in planets if _planet_is_observed(p))
    explored_fraction = observed / total

    owned = len(_owned_planets(state))
    others = _known_other_players(state)

    # We intentionally keep this robust to different state schemas.
    # A map-known unowned/unobserved world is treated as open frontier.
    open_frontier = 0
    contested = 0
    player_id = getattr(state, "player_id", None)
    for p in planets:
        owner = getattr(p, "owner_id", getattr(p, "owner", None))
        observed_p = _planet_is_observed(p)
        if owner in (None, -1):
            if not observed_p or getattr(p, "habitability", None) is not None:
                open_frontier += 1
        elif owner != player_id:
            contested += 1

    open_fraction = open_frontier / total
    contested_fraction = contested / total

    # Frontier pressure rises with contact and with a shrinking pool of open worlds.
    pressure = min(
        1.0,
        0.15 * len(others)
        + 0.65 * contested_fraction
        + 0.35 * max(0.0, explored_fraction - open_fraction)
    )
    saturation = min(
        1.0,
        0.60 * explored_fraction
        + 0.40 * (1.0 - min(1.0, open_fraction * 2.0))
    )

    return FrontierAssessment(
        explored_fraction=explored_fraction,
        owned_planets=owned,
        known_enemy_players=len(others),
        contested_frontier_fraction=contested_fraction,
        open_frontier_fraction=open_fraction,
        frontier_pressure=pressure,
        expansion_saturation=saturation,
    )


def resource_score(planet: Any) -> float | None:
    """
    Coarse normalized resource score from concentration and/or surface minerals.
    Returns None when not enough information is available.
    """
    vals = []
    for attr in ("ironium_conc", "boranium_conc", "germanium_conc"):
        v = getattr(planet, attr, None)
        if v is not None:
            vals.append(max(0.0, min(1.0, float(v) / 100.0)))
    if vals:
        return sum(vals) / len(vals)

    surf = []
    for attr in ("ironium", "boranium", "germanium"):
        v = getattr(planet, attr, None)
        if v is not None:
            # Saturating scale; 50k+ is treated as excellent for comparison.
            surf.append(max(0.0, min(1.0, float(v) / 50000.0)))
    if surf:
        return sum(surf) / len(surf)
    return None


def planet_value_score(planet: Any, preference: PlanetPreference) -> float:
    """
    Returns a 0..1 economic/colonization desirability score.

    Habitability gets the strongest weight when known. Resource richness is
    secondary. Unknown values receive neutral priors rather than zero so the
    scout planner can still investigate them.
    """
    hab = getattr(planet, "habitability", None)
    if hab is None:
        hab_score = 0.50
    else:
        # Stars! hab can be negative for red worlds and positive up to ~100.
        hab_score = max(0.0, min(1.0, (float(hab) + 15.0) / 115.0))

    res = resource_score(planet)
    res_score = 0.50 if res is None else res

    strategic = getattr(planet, "strategic_value", None)
    strategic_score = 0.50 if strategic is None else max(0.0, min(1.0, float(strategic)))

    return 0.55 * hab_score + 0.30 * res_score + 0.15 * strategic_score


def planet_meets_preference(
    planet: Any,
    preference: PlanetPreference,
    *,
    frontier_value: float = 0.0,
) -> bool:
    hab = getattr(planet, "habitability", None)
    res = resource_score(planet)

    if preference.frontier_exception and frontier_value >= 0.80:
        return True

    if preference.min_habitability is not None and hab is not None:
        if float(hab) < preference.min_habitability:
            return False

    if preference.min_resource_score is not None and res is not None:
        if res < preference.min_resource_score:
            return False

    score = planet_value_score(planet, preference)
    # Convert selectivity into a useful threshold while leaving low-selectivity
    # personas willing to expand broadly.
    threshold = 0.25 + 0.55 * max(0.0, min(1.0, preference.selectivity))
    return score >= threshold


class StrategicPhaseManager:
    """
    Shared phase controller.

    All personas begin with aggressive explore/expand behavior. Once the map
    becomes constrained by neighboring empires or the remaining open frontier
    shrinks, the persona's transition_style determines what comes next.
    """

    def decide(self, state: Any, persona: Any) -> PhaseDecision:
        a = assess_frontier(state)

        # Shared opening doctrine.
        min_opening_planets = int(getattr(persona, "opening_min_planets", 4))
        contact_threshold = float(getattr(persona, "frontier_contact_threshold", 0.18))
        saturation_threshold = float(getattr(persona, "expansion_saturation_threshold", 0.55))

        if (
            a.owned_planets < min_opening_planets
            and a.frontier_pressure < contact_threshold
        ):
            phase = StrategicPhase.EARLY_EXPANSION
            reason = "Opening doctrine: aggressively explore and claim viable worlds before borders harden."
        elif (
            a.frontier_pressure < contact_threshold
            and a.expansion_saturation < saturation_threshold
        ):
            phase = StrategicPhase.EARLY_EXPANSION
            reason = "Substantial open frontier remains; continued exploration/expansion has the best macro return."
        else:
            style = getattr(persona, "transition_style", "consolidate")
            if style == "attack":
                phase = StrategicPhase.OPPORTUNISTIC_WAR
                reason = "Expansion frontier is constrained; persona shifts toward selective conquest of favorable targets."
            elif style == "fortify":
                phase = StrategicPhase.FORTIFICATION
                reason = "Expansion frontier is constrained; persona prioritizes border defense and strengthening acquired territory."
            elif style == "technology":
                phase = StrategicPhase.TECH_ACCELERATION
                reason = "Expansion frontier is constrained; persona converts territorial base into a technology advantage."
            elif style == "industry":
                phase = StrategicPhase.INDUSTRIAL_BUILDOUT
                reason = "Expansion frontier is constrained; persona converts territorial base into industrial capacity."
            else:
                phase = StrategicPhase.CONSOLIDATION
                reason = "Expansion frontier is constrained; persona balances development, defense, and selective expansion."

        policy = self._policy_for(phase, persona)
        return PhaseDecision(phase=phase, assessment=a, policy=policy, reason=reason)

    def _policy_for(self, phase: StrategicPhase, persona: Any) -> PhasePolicy:
        # Base multipliers are intentionally strong enough that phase changes
        # materially affect downstream planners.
        table = {
            StrategicPhase.EARLY_EXPANSION: (1.75, 1.80, 0.85, 0.50, 0.45, 0.90),
            StrategicPhase.FRONTIER_CONTACT: (1.10, 1.00, 1.00, 1.05, 0.85, 1.00),
            StrategicPhase.CONSOLIDATION: (0.65, 0.60, 1.45, 1.30, 0.60, 1.10),
            StrategicPhase.OPPORTUNISTIC_WAR: (0.45, 0.55, 0.95, 1.05, 1.85, 1.00),
            StrategicPhase.FORTIFICATION: (0.45, 0.40, 1.30, 1.90, 0.45, 1.00),
            StrategicPhase.TECH_ACCELERATION: (0.50, 0.45, 1.00, 0.90, 0.55, 1.90),
            StrategicPhase.INDUSTRIAL_BUILDOUT: (0.45, 0.45, 1.95, 1.10, 0.50, 1.05),
        }
        e, x, d, f, a, r = table[phase]
        return PhasePolicy(
            phase=phase,
            explore_weight=e,
            expand_weight=x,
            develop_weight=d,
            fortify_weight=f,
            attack_weight=a,
            research_weight=r,
        )
