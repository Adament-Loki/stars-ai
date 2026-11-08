
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from .diplomacy import allied_player_ids, world_faces_hostile
from .util import distance


class StrategicPhase(str, Enum):
    EARLY_EXPANSION = "early_expansion"
    FRONTIER_CONTACT = "frontier_contact"
    CONSOLIDATION = "consolidation"
    OPPORTUNISTIC_WAR = "opportunistic_war"
    FORTIFICATION = "fortification"
    TECH_ACCELERATION = "tech_acceleration"
    INDUSTRIAL_BUILDOUT = "industrial_buildout"
    MIDGAME = "midgame"


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
    hostile_worlds: int = 0
    hostile_pressure: float = 0.0

    @property
    def has_contact(self) -> bool:
        """True only when a hostile fleet or colony is actually on our border."""

        return self.hostile_worlds > 0


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


def _owner_of(obj: Any) -> int | None:
    owner = getattr(obj, "owner_id", None)
    if owner is None:
        owner = getattr(obj, "owner", None)
    if owner is None:
        return None
    try:
        return int(owner)
    except (TypeError, ValueError):
        return None


def _is_ally(state: Any, owner: int | None) -> bool:
    return owner is not None and owner in allied_player_ids(state)


def _known_other_players(state: Any) -> set[int]:
    player_id = _owner_of(state) or getattr(state, "player_id", None)
    try:
        player_id = int(player_id)
    except (TypeError, ValueError):
        player_id = None
    players = set()
    for fleet in getattr(state, "fleets", []) or []:
        owner = _owner_of(fleet)
        if owner is not None and owner != player_id and not _is_ally(state, owner):
            players.add(owner)
    for planet in getattr(state, "planets", []) or []:
        owner = _owner_of(planet)
        if owner is not None and owner not in (-1, player_id) and not _is_ally(state, owner):
            players.add(owner)
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
    owned_planets = _owned_planets(state)
    hostile_worlds = sum(
        1 for planet in owned_planets
        if getattr(planet, "position", None) is not None and world_faces_hostile(state, planet)
    )
    for p in planets:
        owner = _owner_of(p)
        observed_p = _planet_is_observed(p)
        if owner in (None, -1):
            if not observed_p or getattr(p, "habitability", None) is not None:
                open_frontier += 1
        elif owner != player_id and not _is_ally(state, owner) and _near_owned(p, owned_planets):
            contested += 1

    open_fraction = open_frontier / total
    contested_fraction = contested / max(1, owned)

    # Pressure is the share of our worlds with a hostile on the border.
    # A known ally, or a hostile that is nowhere near us, is not pressure.
    local = hostile_worlds / max(1, owned)
    pressure = min(1.0, 0.80 * local + 0.20 * min(1.0, contested_fraction))
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
        hostile_worlds=hostile_worlds,
        hostile_pressure=local,
    )


def _near_owned(planet: Any, owned: list[Any], radius: float = 120.0) -> bool:
    position = getattr(planet, "position", None)
    if position is None:
        return False
    for world in owned:
        other = getattr(world, "position", None)
        if other is not None and distance(position, other) <= radius:
            return True
    return False


def assign_planet_roles(state: Any, *, full: bool) -> dict[int, str]:
    """Give each owned world one midgame job.

    A world with a hostile nearby holds the line. Quiet worlds that are still
    short of the factories their population can run keep industrializing.
    Worlds already near that cap either build ships for the rest of the empire
    or spend their resources on research.
    """

    owned = [
        planet for planet in getattr(state, "planets", []) or []
        if _owner_of(planet) == int(getattr(state, "player_id", 0) or 0)
    ]
    roles: dict[int, str] = {}
    quiet: list[Any] = []
    for planet in owned:
        if getattr(planet, "position", None) is not None and world_faces_hostile(state, planet):
            roles[int(planet.id)] = "hold_line"
        else:
            quiet.append(planet)
    if not full or not quiet:
        return roles

    from .planet_economy import decode_race_economy, installation_caps

    economy = decode_race_economy(getattr(state, "race", None))
    developing: list[Any] = []
    mature: list[Any] = []
    for planet in quiet:
        factories, _mines = installation_caps(int(getattr(planet, "population", 0) or 0), economy)
        built = int(getattr(planet, "factories", 0) or 0)
        at_peak = factories >= 10 and built >= max(2, int(factories * 0.80))
        if at_peak:
            mature.append(planet)
        else:
            developing.append(planet)
            roles[int(planet.id)] = "industrialize"
    def ship_rank(planet: Any) -> tuple[int, int, int]:
        yard = bool(((getattr(planet, "native", {}) or {}).get("starbase_capabilities") or {}).get("can_build_ships"))
        return (int(yard), int(getattr(planet, "population", 0) or 0), int(getattr(planet, "factories", 0) or 0))

    mature.sort(key=ship_rank, reverse=True)
    ship_count = max(1, -(-len(mature) * 2 // 5)) if mature else 0
    for index, planet in enumerate(mature):
        roles[int(planet.id)] = "ships" if index < ship_count else "tech"
    if not mature and developing:
        best = max(developing, key=ship_rank)
        native = getattr(best, "native", {}) or {}
        if (native.get("starbase_capabilities") or {}).get("can_build_ships"):
            roles[int(best.id)] = "ships"
    return roles


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
        min_opening_planets = int(getattr(persona, "opening_min_planets", 8))
        midgame_planets = int(getattr(persona, "midgame_planets", 12))
        saturation_threshold = float(getattr(persona, "expansion_saturation_threshold", 0.55))

        if a.owned_planets < min_opening_planets:
            phase = StrategicPhase.EARLY_EXPANSION
            reason = (
                "Opening doctrine: claim worlds before the border hardens. "
                "Hostile contact does not end early expansion."
            )
        elif (
            a.owned_planets < midgame_planets
            and a.hostile_worlds > 0
            and a.expansion_saturation < saturation_threshold
        ):
            phase = StrategicPhase.FRONTIER_CONTACT
            reason = (
                f"{a.hostile_worlds} owned world(s) have a hostile fleet or colony nearby. "
                "Hold those worlds and keep planting everywhere else."
            )
        else:
            phase = StrategicPhase.MIDGAME
            reason = (
                "Early expansion is over. Industrialize worlds that are short of their "
                "factory cap, put research on mature quiet worlds, build ships where a "
                "yard can support the empire, and hold the line where a hostile is close."
            )

        policy = self._policy_for(phase, a)
        return PhaseDecision(phase=phase, assessment=a, policy=policy, reason=reason)

    def _policy_for(self, phase: StrategicPhase, assessment: FrontierAssessment) -> PhasePolicy:
        # Base multipliers are intentionally strong enough that phase changes
        # materially affect downstream planners.
        table = {
            StrategicPhase.EARLY_EXPANSION: (2.05, 2.30, 0.75, 0.30, 0.35, 0.70),
            StrategicPhase.FRONTIER_CONTACT: (1.35, 1.70, 0.95, 1.05, 0.70, 0.85),
            StrategicPhase.MIDGAME: (0.70, 0.55, 1.45, 1.20, 1.10, 1.25),
            StrategicPhase.CONSOLIDATION: (0.65, 0.60, 1.45, 1.30, 0.60, 1.10),
            StrategicPhase.OPPORTUNISTIC_WAR: (0.45, 0.55, 0.95, 1.05, 1.85, 1.00),
            StrategicPhase.FORTIFICATION: (0.45, 0.40, 1.30, 1.90, 0.45, 1.00),
            StrategicPhase.TECH_ACCELERATION: (0.50, 0.45, 1.00, 0.90, 0.55, 1.90),
            StrategicPhase.INDUSTRIAL_BUILDOUT: (0.45, 0.45, 1.95, 1.10, 0.50, 1.05),
        }
        e, x, d, f, a, r = table[phase]
        if phase == StrategicPhase.FRONTIER_CONTACT and assessment.hostile_pressure >= 0.5:
            x, f = 1.15, 1.55
        return PhasePolicy(
            phase=phase,
            explore_weight=e,
            expand_weight=x,
            develop_weight=d,
            fortify_weight=f,
            attack_weight=a,
            research_weight=r,
        )
