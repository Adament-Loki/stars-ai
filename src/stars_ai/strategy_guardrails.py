"""Limits the director cannot talk the rule engine out of."""
from __future__ import annotations

from typing import Any

from stars_ai.strategy_digest import estimate_tokens

DEFAULT_MAX_TOKENS = 12_000
DEFAULT_MAX_COST_USD = 0.25
DEFAULT_MAX_SECONDS = 20.0
_COST_PER_1K_TOKENS = 0.01


def rejection(state: Any, option: dict[str, Any]) -> str | None:
    """Return why this menu choice is forbidden, or None when it may apply."""
    payload = option.get("payload") or {}
    category = option.get("category")
    if category == "colonize":
        planet = _planet(state, int(payload.get("destination_planet_id", -1)))
        if planet is None or planet.owner is not None or bool((planet.native or {}).get("has_starbase")):
            return "refusing colonization of an owned planet or a world that already has a starbase"
    if category == "front" and payload.get("action") == "attack":
        decision = str(((getattr(state, "native", {}) or {}).get("turn_combat") or {}).get("decision") or "")
        if decision == "retreat_and_preserve":
            return "combat_engagement says RETREAT_AND_PRESERVE"
        home = _home(state)
        if home is not None and int(payload.get("planet_id", -1)) != int(home.id) and _home_is_the_only_guard(state, home):
            return "refusing to leave the homeworld undefended"
    return None


def budget_block(state: Any, digest: dict[str, Any], menu: dict[str, Any]) -> str | None:
    """Refuse the call when this turn would exceed the token, cost, or time cap."""
    native = getattr(state, "native", {}) or {}
    tokens = estimate_tokens({"digest": digest, "menu": menu})
    max_tokens = int(native.get("llm_director_max_tokens", DEFAULT_MAX_TOKENS))
    if tokens > max_tokens:
        return f"token budget {tokens} exceeds {max_tokens}"
    cost = tokens / 1000.0 * _COST_PER_1K_TOKENS
    max_cost = float(native.get("llm_director_max_cost_usd", DEFAULT_MAX_COST_USD))
    if cost > max_cost:
        return f"estimated cost ${cost:.4f} exceeds ${max_cost:.4f}"
    max_seconds = float(native.get("llm_director_max_seconds", DEFAULT_MAX_SECONDS))
    if max_seconds <= 0:
        return "wall-clock budget is zero"
    return None


def _planet(state: Any, planet_id: int):
    return next((planet for planet in state.planets if int(planet.id) == planet_id), None)


def _home(state: Any):
    for planet in state.planets:
        if planet.owner == state.player_id and (planet.native or {}).get("is_homeworld"):
            return planet
    return None


def _home_is_the_only_guard(state: Any, home) -> bool:
    guards = [
        fleet for fleet in state.fleets
        if fleet.owner == state.player_id and fleet.role in {"combat", "scout"}
    ]
    if not guards:
        return int(home.defenses or 0) <= 0
    return all(
        abs(float(fleet.position.x) - float(home.position.x)) <= 0.5
        and abs(float(fleet.position.y) - float(home.position.y)) <= 0.5
        for fleet in guards
    ) and int(home.defenses or 0) <= 0
