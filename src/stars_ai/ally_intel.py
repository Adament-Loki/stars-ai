"""Share allied Stars! seats' sightings before a turn is planned.

Each player M-file contains only that seat's scan. A configured ally has
already been declared Friend in the order file, which is the game's own ally
setting, but that declaration does not arrive until the host runs. This module
copies the ally's current observations into the planning state so the seats
can use one another's map during the same turn.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re

from .adapters.native_core_adapter import NativeCoreTurnAdapter
from .models import GameState


def ally_turn_path(m_path: Path, ally_id: int) -> Path:
    """Return the sibling ``GAME.mN`` path for one allied seat."""

    name = m_path.name
    match = re.search(r"\.m\d+$", name, flags=re.IGNORECASE)
    if match is None:
        raise ValueError(f"Cannot derive an allied M file from {name}.")
    return m_path.with_name(f"{name[:match.start()]}.m{int(ally_id)}")


def load_ally_states(
    m_path: Path,
    xy_path: Path,
    ally_ids: list[int],
) -> tuple[list[GameState], list[str]]:
    """Read each allied M-file. Missing files are reported and skipped."""

    states: list[GameState] = []
    notes: list[str] = []
    for ally_id in ally_ids:
        path = ally_turn_path(m_path, int(ally_id))
        if not path.is_file():
            notes.append(f"Ally P{ally_id} has no turn file at {path.name}.")
            continue
        try:
            states.append(NativeCoreTurnAdapter(xy_path).read_state(path, int(ally_id)))
        except Exception as exc:
            notes.append(f"Ally P{ally_id} turn file could not be read: {exc}.")
    return states, notes


def share_ally_observations(state: GameState, ally_states: list[GameState]) -> dict:
    """Fill planets and foreign fleets this seat has not observed itself.

    An observation this seat already has is left unchanged. Allied fleets and
    foreign contacts the ally can see are added when their fleet id is new.
    The returned summary is also stored on ``state.native['ally_intel']``.
    """

    planets = {int(planet.id): planet for planet in state.planets}
    shared_planets: list[int] = []
    for ally in ally_states:
        for source in ally.planets:
            if not source.observed:
                continue
            current = planets.get(int(source.id))
            if current is not None and current.observed:
                continue
            native = dict(source.native)
            native["shared_from_ally"] = int(ally.player_id)
            if current is None:
                current = replace(source, native=native, observed=True)
                state.planets.append(current)
                planets[int(current.id)] = current
            else:
                current.name = source.name or current.name
                current.owner = source.owner
                current.habitability = source.habitability
                current.population = source.population
                current.factories = source.factories
                current.mines = source.mines
                current.defenses = source.defenses
                current.resources = source.resources
                current.ironium = source.ironium
                current.boranium = source.boranium
                current.germanium = source.germanium
                current.observed = True
                current.native = native
            shared_planets.append(int(source.id))

    known_fleets = {int(fleet.id) for fleet in state.fleets}
    shared_fleets: list[int] = []
    for ally in ally_states:
        for source in ally.fleets:
            if int(source.owner) == int(state.player_id) or int(source.id) in known_fleets:
                continue
            native = dict(source.native)
            native["shared_from_ally"] = int(ally.player_id)
            state.fleets.append(replace(source, native=native))
            known_fleets.add(int(source.id))
            shared_fleets.append(int(source.id))

    summary = {
        "allies": [int(ally.player_id) for ally in ally_states],
        "shared_planets": shared_planets,
        "shared_fleets": shared_fleets,
    }
    state.native["ally_intel"] = summary
    return summary
