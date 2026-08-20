from __future__ import annotations

from dataclasses import asdict, dataclass
import math

from .models import GameState


# Stars! standard terraforming requires the related field (gravity=Propulsion,
# temperature=Energy, radiation=Weapons) plus Biotechnology. Total Terraforming
# uses Biotechnology alone. Values are the maximum movement from original hab.
STANDARD_LEVELS = ((1, 1, 3), (5, 2, 7), (10, 3, 11), (16, 4, 15))
TOTAL_TERRAFORMING_LEVELS = (
    (0, 3), (3, 5), (6, 7), (9, 10),
    (13, 15), (17, 20), (22, 25), (25, 30),
)


@dataclass(frozen=True)
class TerraformingPotential:
    current_habitability: int | None
    tech_habitability: int | None
    eventual_habitability: int | None
    planning_habitability: int | None
    current_environment: tuple[int, int, int] | None
    tech_environment: tuple[int, int, int] | None
    eventual_environment: tuple[int, int, int] | None
    tech_limits: tuple[int, int, int]
    eventual_limits: tuple[int, int, int]
    tech_steps: int
    eventual_steps: int
    total_terraforming: bool
    resource_cost_per_step: int

    @property
    def tech_gain(self) -> int:
        if self.current_habitability is None or self.tech_habitability is None:
            return 0
        return max(0, int(self.tech_habitability) - int(self.current_habitability))

    @property
    def eventual_gain(self) -> int:
        if self.current_habitability is None or self.eventual_habitability is None:
            return 0
        return max(0, int(self.eventual_habitability) - int(self.current_habitability))

    def to_dict(self):
        return asdict(self) | {
            "tech_gain": self.tech_gain,
            "eventual_gain": self.eventual_gain,
        }


def _triplet(value) -> tuple[int, int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    if any(x is None for x in value[:3]):
        return None
    return tuple(int(x) for x in value[:3])


def _race_hab_data(race):
    native = race.native or {}
    centers = _triplet(native.get("hab_center"))
    lows = _triplet(native.get("hab_low"))
    highs = _triplet(native.get("hab_high"))
    immune_raw = native.get("hab_immune")
    immune = (
        tuple(bool(x) for x in immune_raw[:3])
        if isinstance(immune_raw, (list, tuple)) and len(immune_raw) >= 3
        else None
    )
    if centers is None or lows is None or highs is None or immune is None:
        return None
    return centers, lows, highs, immune


def habitability_for_environment(race, environment) -> int | None:
    """Calculate race-specific Stars! habitability for a normalized environment."""
    env = _triplet(environment)
    data = _race_hab_data(race)
    if env is None or data is None:
        return None
    if bool((race.native or {}).get("universal_hab", False)):
        return 100

    centers, lows, highs, immune = data
    points = 0.0
    red = 0
    ideality = 10000.0
    for axis, value in enumerate(env):
        if immune[axis]:
            points += 10000.0
            continue
        center, low, high = centers[axis], lows[axis], highs[axis]
        if low <= value <= high:
            if center > value:
                radius = max(1, center - low)
                offset = center - value
            else:
                radius = max(1, high - center)
                offset = value - center
            from_ideal = 100.0 - abs(value - center) * 100.0 / radius
            poor = offset * 2 - radius
            points += max(0.0, from_ideal) ** 2
            if poor > 0:
                ideality *= max(0.0, (radius * 2 - poor) / (radius * 2))
        else:
            red += min(int(value - high if value > high else low - value), 15)
    if red:
        return -red
    value = int(math.sqrt(points / 3.0) + 0.9)
    return int(value * ideality / 10000.0)


def _standard_limit(related_tech: int, biotechnology: int) -> int:
    limit = 0
    for field_required, bio_required, amount in STANDARD_LEVELS:
        if related_tech >= field_required and biotechnology >= bio_required:
            limit = amount
    return limit


def terraforming_limits(state: GameState, *, eventual: bool = False) -> tuple[int, int, int]:
    native = state.race.native or {}
    immune_raw = native.get("hab_immune") or [False, False, False]
    immune = tuple(bool(x) for x in list(immune_raw)[:3])
    immune = immune + (False,) * (3 - len(immune))
    total = "TT" in set(native.get("lrts", []) or [])
    if eventual:
        base = 30 if total else 15
        return tuple(0 if immune[i] else base for i in range(3))

    bio = int(state.tech.biotechnology or 0)
    if total:
        limit = 0
        for bio_required, amount in TOTAL_TERRAFORMING_LEVELS:
            if bio >= bio_required:
                limit = amount
        return tuple(0 if immune[i] else limit for i in range(3))

    related = (
        int(state.tech.propulsion or 0),
        int(state.tech.energy or 0),
        int(state.tech.weapons or 0),
    )
    return tuple(
        0 if immune[i] else _standard_limit(related[i], bio)
        for i in range(3)
    )


def _best_environment(current, original, centers, limits, immune):
    out = []
    for i in range(3):
        if immune[i]:
            out.append(int(current[i]))
            continue
        base = int(original[i])
        center = int(centers[i])
        limit = int(limits[i])
        bounded = max(base - limit, min(base + limit, center))
        # Never value a world below its already-achieved environment if another
        # race or earlier technology moved this axis closer to our ideal.
        chosen = int(current[i]) if abs(int(current[i]) - center) <= abs(bounded - center) else bounded
        out.append(chosen)
    return tuple(out)


def evaluate_terraforming(state: GameState, planet) -> TerraformingPotential:
    native = planet.native or {}
    current_env = _triplet(native.get("environment"))
    original_env = _triplet(native.get("original_environment")) or current_env
    data = _race_hab_data(state.race)
    total = "TT" in set((state.race.native or {}).get("lrts", []) or [])
    cost = 70 if total else 100
    tech_limits = terraforming_limits(state)
    eventual_limits = terraforming_limits(state, eventual=True)

    current_hab = int(planet.habitability) if planet.habitability is not None else None
    if current_env is not None:
        calculated = habitability_for_environment(state.race, current_env)
        current_hab = calculated if calculated is not None else current_hab

    if current_env is None or original_env is None or data is None:
        return TerraformingPotential(
            current_hab, current_hab, current_hab, current_hab,
            current_env, current_env, current_env,
            tech_limits, eventual_limits, 0, 0, total, cost,
        )

    centers, _, _, immune = data
    tech_env = _best_environment(current_env, original_env, centers, tech_limits, immune)
    eventual_env = _best_environment(current_env, original_env, centers, eventual_limits, immune)
    tech_hab = habitability_for_environment(state.race, tech_env)
    eventual_hab = habitability_for_environment(state.race, eventual_env)
    current_hab = habitability_for_environment(state.race, current_env)

    # Long-term potential is strategically useful, but future tech is not free.
    # Discount it so currently green worlds still beat speculative terraforming bets.
    turn = max(0, int(state.year) - 2400)
    future_weight = 0.55 if turn <= 15 else (0.70 if turn <= 40 else 0.85)
    now = int(tech_hab if tech_hab is not None else current_hab or 0)
    eventual_value = int(eventual_hab if eventual_hab is not None else now)
    planning = max(now, round(now + max(0, eventual_value - now) * future_weight))

    return TerraformingPotential(
        current_hab, tech_hab, eventual_hab, planning,
        current_env, tech_env, eventual_env,
        tech_limits, eventual_limits,
        sum(abs(tech_env[i] - current_env[i]) for i in range(3)),
        sum(abs(eventual_env[i] - current_env[i]) for i in range(3)),
        total, cost,
    )
