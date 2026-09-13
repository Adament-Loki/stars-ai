"""Conservative population-capture target selection.

Dropping a decisive colonist load is a useful expansion action against a small
ordinary colony.  It is intentionally kept separate from combat planning: the
caller still has to provide one large, empty, fuel-safe transport and retain a
healthy breeder population.  This module only identifies the targets and the
load required to overwhelm their observed population.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


COLONISTS_PER_KT = 100
MINIMUM_CAPTURE_COLONISTS = 80_000
"""Small-colony capture packet; 800 kT in native cargo units."""

@dataclass(frozen=True)
class PopulationCaptureTarget:
    planet_id: int
    owner_id: int
    estimated_population: int
    required_colonists: int
    required_kt: int
    target_prt_id: int | None
    target_prt_name: str | None


def _foreign_trait_record(state: Any, owner_id: int) -> dict[str, Any]:
    native = getattr(state, "native", {}) or {}
    for key in ("foreign_race_traits", "foreign_player_traits", "foreign_races"):
        records = native.get(key) or {}
        if isinstance(records, dict):
            record = records.get(str(owner_id), records.get(owner_id, {}))
            if isinstance(record, dict):
                return record
    return {}


def foreign_prt(state: Any, planet: Any) -> tuple[int | None, str | None]:
    """Return observed foreign trait metadata when the turn supplies it.

    Standard Stars! M files commonly reveal only the current player's full
    race record.  Unknown is therefore represented as ``None`` rather than
    guessed; when a scout/history adapter supplies foreign trait data, it is
    honored here before an order is emitted.
    """
    native = getattr(planet, "native", {}) or {}
    record = _foreign_trait_record(state, int(getattr(planet, "owner", -1) or -1))
    raw_id = native.get("owner_prt_id", native.get("prt_id", record.get("prt_id")))
    try:
        prt_id = int(raw_id) if raw_id is not None else None
    except (TypeError, ValueError):
        prt_id = None
    raw_name = native.get("owner_prt_name", native.get("prt_name", record.get("prt_name")))
    prt_name = str(raw_name) if raw_name else None
    return prt_id, prt_name


def is_alternate_reality_target(state: Any, planet: Any) -> bool:
    prt_id, prt_name = foreign_prt(state, planet)
    return prt_id == 8 or "alternate reality" in str(prt_name or "").lower()


def observed_population_estimate(planet: Any) -> int:
    """Use the most conservative observed or tactical population estimate.

    A newly captured world often exposes its enemy owner but not its detailed
    population in the first M file.  Memory supplies the last friendly count
    as explicitly stale tactical intelligence; it is a floor for planning a
    recapture, never a claim that we still control the world.
    """
    exact = max(0, int(getattr(planet, "population", 0) or 0))
    native = getattr(planet, "native", {}) or {}
    estimates = [exact]
    for key in ("population_estimate", "tactical_population_estimate"):
        estimate = native.get(key)
        try:
            # Stars! foreign population estimates are measured in
            # 400-colonist units. Tactical memory is normalized colonists.
            estimates.append(
                max(0, int(estimate or 0))
                if key == "tactical_population_estimate"
                else max(0, int(estimate or 0) * 400)
            )
        except (TypeError, ValueError):
            continue
    return max(estimates, default=0)


def is_recently_lost_world(planet: Any) -> bool:
    """Whether current enemy ownership revoked our local control this turn."""
    native = getattr(planet, "native", {}) or {}
    return bool(native.get("recently_lost_by_player"))


def population_capture_targets(state: Any) -> list[PopulationCaptureTarget]:
    """Return observed, non-AR foreign colonies whose capture load is calculable.

    Hull capacity, fuel range, and protected-source population are evaluated
    later by the transport scheduler.  Keeping them out of this discovery pass
    avoids a magic enemy-population ceiling that could suppress a legal
    counter-capture by a larger available freighter.
    """
    targets: list[PopulationCaptureTarget] = []
    for planet in getattr(state, "planets", []) or []:
        owner = getattr(planet, "owner", None)
        if owner is None or int(owner) == int(getattr(state, "player_id", -1)):
            continue
        if not bool(getattr(planet, "observed", False)):
            continue
        if getattr(planet, "habitability", None) is not None and int(planet.habitability) <= 0:
            continue
        if is_alternate_reality_target(state, planet):
            continue
        population = observed_population_estimate(planet)
        if population <= 0:
            continue
        required = max(MINIMUM_CAPTURE_COLONISTS, population * 4)
        prt_id, prt_name = foreign_prt(state, planet)
        targets.append(PopulationCaptureTarget(
            planet_id=int(planet.id),
            owner_id=int(owner),
            estimated_population=int(population),
            required_colonists=int(required),
            required_kt=int((required + COLONISTS_PER_KT - 1) // COLONISTS_PER_KT),
            target_prt_id=prt_id,
            target_prt_name=prt_name,
        ))
    return sorted(
        targets,
        key=lambda target: (target.required_colonists, target.planet_id),
    )
