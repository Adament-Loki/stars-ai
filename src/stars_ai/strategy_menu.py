"""Legal choices a strategy director may pick.

Every option maps to an order kind the native writer already treats as
VALIDATED or PARTIAL. BLOCKED kinds, including battle plans and mineral
packets, are never offered. The rule engine marks one choice per decision.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from stars_ai.core.models import GameState, Planet
from stars_ai.native_capabilities import capability
from stars_ai.research_planner import FIELDS
from stars_ai.strategy_digest import THREAT_RADIUS_LY

OFFERED_STATUSES = {"VALIDATED", "PARTIAL"}
QUEUE_TEMPLATES = (
    ("mines", "mine", "Build mines before other industry."),
    ("factories", "factory", "Build factories after the mine floor."),
    ("defenses", "defense", "Build planetary defenses."),
)


@dataclass(frozen=True)
class MenuOption:
    option_id: str
    category: str
    order_kind: str
    capability: str
    summary: str
    engine_choice: bool
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_option_menu(state: GameState) -> dict[str, Any]:
    """Collect the engine's legal menu and the choice it would make alone."""
    options = [
        *_research_options(state),
        *_colonize_options(state),
        *_queue_options(state),
        *_front_options(state),
    ]
    options = [option for option in options if _offered(option.order_kind)]
    return {
        "options": [option.to_dict() for option in options],
        "engine_choices": [option.option_id for option in options if option.engine_choice],
    }


def _offered(order_kind: str) -> bool:
    return capability(order_kind).status in OFFERED_STATUSES


def _research_options(state: GameState) -> list[MenuOption]:
    current = str((state.native or {}).get("current_research_field") or "energy")
    if current not in FIELDS:
        current = "energy"
    return [
        MenuOption(
            option_id=f"research:{field_name}",
            category="research",
            order_kind="research_change",
            capability=capability("research_change").status,
            summary=f"Research {field_name}.",
            engine_choice=field_name == current,
            payload={"current_field": field_name, "next_field": field_name, "allocation_percent": 15},
        )
        for field_name in FIELDS
    ]


def _colonize_options(state: GameState) -> list[MenuOption]:
    home = _home(state)
    candidates = []
    for planet in state.planets:
        if planet.owner is not None:
            continue
        if not planet.observed:
            continue
        if (planet.native or {}).get("has_starbase"):
            continue
        habitability = int(planet.habitability or 0)
        if habitability <= 0:
            continue
        distance = _distance(home, planet) if home is not None else 0.0
        candidates.append((distance, habitability, planet))
    candidates.sort(key=lambda row: (row[0], -row[1], int(row[2].id)))
    chosen = next((planet.id for _distance, habitability, planet in candidates if habitability >= 40), None)
    if chosen is None and candidates:
        chosen = candidates[0][2].id
    return [
        MenuOption(
            option_id=f"colonize:{int(planet.id)}",
            category="colonize",
            order_kind="colony_operation",
            capability=capability("colony_operation").status,
            summary=f"Colonize {planet.name} at {habitability}% habitability.",
            engine_choice=int(planet.id) == chosen,
            payload={"destination_planet_id": int(planet.id), "habitability": habitability},
        )
        for distance, habitability, planet in candidates[:8]
    ]


def _queue_options(state: GameState) -> list[MenuOption]:
    home = _home(state)
    if home is None:
        owned = [p for p in state.planets if p.owner == state.player_id]
        home = owned[0] if owned else None
    if home is None:
        return []
    return [
        MenuOption(
            option_id=f"queue:{int(home.id)}:{name}",
            category="build_queue",
            order_kind="set_planet_queue",
            capability=capability("set_planet_queue").status,
            summary=summary,
            engine_choice=name == "mines",
            payload={"planet_id": int(home.id), "queue": [{"item": item, "quantity": 10}]},
        )
        for name, item, summary in QUEUE_TEMPLATES
    ]


def _front_options(state: GameState) -> list[MenuOption]:
    owned = [p for p in state.planets if p.owner == state.player_id]
    fronts: dict[int, Planet] = {}
    for fleet in state.fleets:
        if fleet.owner == state.player_id:
            continue
        nearest = None
        best = None
        for planet in owned:
            distance = math.hypot(
                float(planet.position.x) - float(fleet.position.x),
                float(planet.position.y) - float(fleet.position.y),
            )
            if best is None or distance < best:
                best = distance
                nearest = planet
        if nearest is None or best is None or best > THREAT_RADIUS_LY:
            continue
        fronts[int(nearest.id)] = nearest
    report = (state.native or {}).get("turn_combat") or {}
    decision = str(report.get("decision") or "")
    if decision == "retreat_and_preserve":
        engine = "retreat"
    elif decision in {"fight_now", "build_current"} and report.get("offensive", True):
        engine = "attack"
    else:
        engine = "hold"
    options = []
    for planet_id, planet in sorted(fronts.items()):
        for action, summary in (
            ("attack", f"Move to contest the front at {planet.name}."),
            ("hold", f"Hold the front at {planet.name}."),
            ("retreat", f"Retreat from the front at {planet.name} and preserve the fleet."),
        ):
            options.append(MenuOption(
                option_id=f"front:{planet_id}:{action}",
                category="front",
                order_kind="move_fleet",
                capability=capability("move_fleet").status,
                summary=summary,
                engine_choice=action == engine,
                payload={"planet_id": planet_id, "action": action},
            ))
    return options


def _home(state: GameState) -> Planet | None:
    for planet in state.planets:
        if planet.owner == state.player_id and (planet.native or {}).get("is_homeworld"):
            return planet
    return None


def _distance(origin: Planet, target: Planet) -> float:
    return math.hypot(
        float(origin.position.x) - float(target.position.x),
        float(origin.position.y) - float(target.position.y),
    )
