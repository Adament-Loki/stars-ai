from __future__ import annotations

from .models import GameState, Position
from .util import distance


def homeworld_planets(state: GameState):
    """Return the empire's actual homeworld(s), with a stable legacy fallback."""
    owned = [p for p in state.planets if p.owner == state.player_id]
    flagged = [p for p in owned if bool((p.native or {}).get("is_homeworld", False))]
    if flagged:
        return flagged
    if not owned:
        return []
    # Synthetic/legacy states do not carry is_homeworld. Population is the most
    # stable proxy and avoids letting a newly founded frontier world redefine home.
    return [max(owned, key=lambda p: (int(p.population or 0), -int(p.id)))]


def homeworld_center(state: GameState) -> Position:
    homes = homeworld_planets(state)
    points = homes or [p for p in state.planets if p.owner == state.player_id]
    if not points:
        return Position(0.0, 0.0)
    return Position(
        sum(float(p.position.x) for p in points) / len(points),
        sum(float(p.position.y) for p in points) / len(points),
    )


def distance_from_homeworld(state: GameState, position: Position) -> float:
    homes = homeworld_planets(state)
    if not homes:
        return 0.0
    return min(distance(position, p.position) for p in homes)
