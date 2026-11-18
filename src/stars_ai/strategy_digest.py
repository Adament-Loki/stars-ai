"""Compact per-turn brief for an optional strategy director.

The digest is JSON built from a parsed turn. It is the only game picture a
later language-model director is allowed to see. It never contains orders.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from stars_ai.core.models import Fleet, GameState, Planet

TOKEN_BUDGET = 8_000
THREAT_RADIUS_LY = 150.0


def estimate_tokens(payload: dict[str, Any]) -> int:
    """Approximate tokens as one per four JSON characters."""
    text = json.dumps(payload, separators=(",", ":"), default=str)
    return max(1, len(text) // 4)


def digest_saved_turn(
    m_path: Path | str,
    xy_path: Path | str,
    h_path: Path | str | None = None,
    *,
    player_id: int = 1,
) -> dict[str, Any]:
    """Build a digest from a player turn file, the map, and optional history."""
    from stars_ai.adapters.native_core_adapter import NativeCoreTurnAdapter
    from stars_ai.adapters.stars_native import read_blocks

    m_path = Path(m_path)
    xy_path = Path(xy_path)
    state = NativeCoreTurnAdapter(xy_path=xy_path).read_state(m_path, player_id)
    history: dict[str, Any] | None = None
    if h_path is not None:
        header, _blocks, _ = read_blocks(Path(h_path))
        history = {"year": int(header.year), "turn": int(header.turn)}
    digest = build_strategy_digest(state)
    digest["sources"] = {
        "m": str(m_path),
        "xy": str(xy_path),
        "h": None if history is None else {**history, "path": str(h_path)},
    }
    return digest


def build_strategy_digest(state: GameState) -> dict[str, Any]:
    """Turn one normalized player state into a budget-capped JSON object."""
    native = state.native or {}
    header = native.get("header") or {}
    year = int(state.year)
    turn = header.get("turn")
    if turn is None:
        turn = max(0, year - 2400)
    ours = [p for p in state.planets if p.owner == state.player_id]
    enemies = [p for p in state.planets if p.owner not in (None, state.player_id) and p.observed]
    our_fleets = [f for f in state.fleets if f.owner == state.player_id]
    enemy_fleets = [f for f in state.fleets if f.owner != state.player_id]
    digest: dict[str, Any] = {
        "turn": int(turn),
        "year": year,
        "player_id": int(state.player_id),
        "score": _score(native),
        "tech": {
            "energy": int(state.tech.energy),
            "weapons": int(state.tech.weapons),
            "propulsion": int(state.tech.propulsion),
            "construction": int(state.tech.construction),
            "electronics": int(state.tech.electronics),
            "biotechnology": int(state.tech.biotechnology),
        },
        "research": {
            "current_field": native.get("current_research_field"),
            "next_field": native.get("next_research_field"),
            "allocation_percent": native.get("research_allocation_percent"),
        },
        "our_planets": [_planet(p, owned=True) for p in sorted(ours, key=lambda p: int(p.id))],
        "our_fleets": [_fleet(state, f) for f in sorted(our_fleets, key=lambda f: int(f.id))],
        "enemy_planets": [_planet(p, owned=False) for p in sorted(enemies, key=lambda p: int(p.id))],
        "enemy_fleets": [_fleet(state, f) for f in sorted(enemy_fleets, key=lambda f: (int(f.owner), int(f.id)))],
        "threats": _threats(ours, enemy_fleets),
        "unobserved_planet_count": sum(1 for p in state.planets if not p.observed),
    }
    return _fit_budget(digest)


def _score(native: dict[str, Any]) -> dict[str, Any]:
    own = native.get("current_player_score") or {}
    rivals = []
    if native.get("score_visibility") == "public":
        rivals = [
            {
                "player_id": int(row.get("player_id", 0)),
                "rank": int(row.get("rank", 0)),
                "score": int(row.get("score", 0)),
                "planets": int(row.get("planets", 0)),
            }
            for row in native.get("player_scores") or []
        ]
    return {
        "visibility": native.get("score_visibility") or "unknown",
        "rank": own.get("rank"),
        "score": own.get("score"),
        "planets": own.get("planets"),
        "tech_total": own.get("tech_total"),
        "rivals": rivals,
    }


def _planet(planet: Planet, *, owned: bool) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": int(planet.id),
        "name": planet.name,
        "owner": planet.owner,
        "x": round(float(planet.position.x), 1),
        "y": round(float(planet.position.y), 1),
        "population": int(planet.population or 0),
        "ironium": int(planet.ironium or 0),
        "boranium": int(planet.boranium or 0),
        "germanium": int(planet.germanium or 0),
        "defenses": int(planet.defenses or 0),
    }
    if owned:
        row["factories"] = int(planet.factories or 0)
        row["mines"] = int(planet.mines or 0)
        row["homeworld"] = bool((planet.native or {}).get("is_homeworld") or (planet.native or {}).get("homeworld"))
    return row


def _fleet(state: GameState, fleet: Fleet) -> dict[str, Any]:
    native = fleet.native or {}
    cargo = native.get("cargo") or {}
    return {
        "id": int(fleet.id),
        "owner": int(fleet.owner),
        "name": fleet.name,
        "role": fleet.role,
        "x": round(float(fleet.position.x), 1),
        "y": round(float(fleet.position.y), 1),
        "designs": _designs(state, fleet),
        "cargo": {
            "ironium": int(cargo.get("ironium", 0) or 0),
            "boranium": int(cargo.get("boranium", 0) or 0),
            "germanium": int(cargo.get("germanium", 0) or 0),
            "population": int(cargo.get("population", 0) or 0),
        },
        "fuel": int(native.get("fuel", 0) or 0),
        "warp": fleet.destination_warp,
        "mass": native.get("mass"),
    }


def _designs(state: GameState, fleet: Fleet) -> list[dict[str, Any]]:
    counts = list((fleet.native or {}).get("ship_count") or [])
    catalog = {
        int(row.get("design_number", -1)): row
        for row in (state.native or {}).get("designs") or []
        if isinstance(row, dict)
    }
    named = []
    for slot, count in enumerate(counts):
        if not count:
            continue
        design = catalog.get(slot) or {}
        named.append({
            "slot": slot,
            "count": int(count),
            "name": design.get("name") or design.get("hull_name") or f"slot-{slot}",
        })
    return named


def _threats(owned: list[Planet], enemy_fleets: list[Fleet]) -> list[dict[str, Any]]:
    threats = []
    for fleet in enemy_fleets:
        nearest = _nearest(owned, fleet)
        if nearest is None:
            continue
        planet, distance = nearest
        if distance > THREAT_RADIUS_LY:
            continue
        threats.append({
            "fleet_id": int(fleet.id),
            "owner": int(fleet.owner),
            "nearest_planet_id": int(planet.id),
            "nearest_planet": planet.name,
            "distance_ly": round(distance, 1),
        })
    threats.sort(key=lambda row: (row["distance_ly"], row["owner"], row["fleet_id"]))
    return threats


def _nearest(planets: list[Planet], fleet: Fleet) -> tuple[Planet, float] | None:
    best: tuple[Planet, float] | None = None
    for planet in planets:
        distance = math.hypot(
            float(planet.position.x) - float(fleet.position.x),
            float(planet.position.y) - float(fleet.position.y),
        )
        if best is None or distance < best[1]:
            best = (planet, distance)
    return best


def _fit_budget(digest: dict[str, Any]) -> dict[str, Any]:
    """Drop the least decision-relevant rows until the brief fits the budget."""
    guards = 0
    while estimate_tokens(digest) > TOKEN_BUDGET and guards < 12:
        guards += 1
        digest["truncated"] = True
        if len(digest["enemy_planets"]) > 8:
            digest["enemy_planets"] = digest["enemy_planets"][: max(8, len(digest["enemy_planets"]) // 2)]
        elif len(digest["enemy_fleets"]) > 12:
            digest["enemy_fleets"] = digest["enemy_fleets"][: max(12, len(digest["enemy_fleets"]) // 2)]
        elif len(digest["our_fleets"]) > 20:
            digest["our_fleets"] = digest["our_fleets"][: max(20, len(digest["our_fleets"]) // 2)]
        else:
            for fleet in digest["our_fleets"] + digest["enemy_fleets"]:
                fleet.pop("designs", None)
            break
    digest["token_estimate"] = estimate_tokens(digest)
    return digest
