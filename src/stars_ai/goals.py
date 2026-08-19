from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import GameState


class Goal(Protocol):
    goal_id: str
    priority: float

    def progress(self, state: GameState) -> float: ...
    def is_complete(self, state: GameState) -> bool: ...
    def apply(self, state: GameState, objectives: dict[str, float], research: dict[str, float], missions: dict[str, float], planets: dict[str, float]) -> str: ...


@dataclass(frozen=True)
class ReachTechGoal:
    field: str
    target_level: int
    priority: float = 1.0
    goal_id: str = "reach-tech"

    def __post_init__(self):
        if self.field not in {"energy", "weapons", "propulsion", "construction", "electronics", "biotechnology"}:
            raise ValueError(f"Unknown research field: {self.field}")
        object.__setattr__(self, "goal_id", f"reach-tech:{self.field}:{self.target_level}")

    def progress(self, state: GameState) -> float:
        current = getattr(state.tech, self.field)
        return min(1.0, current / max(1, self.target_level))

    def is_complete(self, state: GameState) -> bool:
        return getattr(state.tech, self.field) >= self.target_level

    def apply(self, state, objectives, research, missions, planets) -> str:
        current = getattr(state.tech, self.field)
        if current >= self.target_level:
            return f"{self.goal_id} complete ({current}/{self.target_level})."
        gap = self.target_level - current
        objectives["research"] = objectives.get("research", 1.0) * (1.0 + 0.20 * self.priority)
        research[self.field] = research.get(self.field, 1.0) * (1.0 + min(1.5, 0.30 * gap) * self.priority)
        return f"Goal {self.goal_id}: {current}/{self.target_level}; focus {self.field}."


@dataclass(frozen=True)
class OwnPlanetsGoal:
    target_count: int
    priority: float = 1.0
    goal_id: str = "own-planets"

    def __post_init__(self):
        object.__setattr__(self, "goal_id", f"own-planets:{self.target_count}")

    def _count(self, state: GameState) -> int:
        return sum(1 for p in state.planets if p.owner == state.player_id)

    def progress(self, state: GameState) -> float:
        return min(1.0, self._count(state) / max(1, self.target_count))

    def is_complete(self, state: GameState) -> bool:
        return self._count(state) >= self.target_count

    def apply(self, state, objectives, research, missions, planets) -> str:
        count = self._count(state)
        if count >= self.target_count:
            return f"{self.goal_id} complete ({count}/{self.target_count})."
        objectives["expand"] = objectives.get("expand", 1.0) * (1.0 + 0.35 * self.priority)
        objectives["scout"] = objectives.get("scout", 1.0) * (1.0 + 0.15 * self.priority)
        missions["colonize"] = missions.get("colonize", 1.0) * (1.0 + 0.35 * self.priority)
        missions["scan"] = missions.get("scan", 1.0) * (1.0 + 0.15 * self.priority)
        research["propulsion"] = research.get("propulsion", 1.0) * (1.0 + 0.15 * self.priority)
        return f"Goal {self.goal_id}: {count}/{self.target_count}; increase expansion capacity."


@dataclass(frozen=True)
class ExploreGalaxyGoal:
    target_fraction: float = 0.75
    priority: float = 1.0
    goal_id: str = "explore-galaxy"

    def __post_init__(self):
        if not 0 < self.target_fraction <= 1:
            raise ValueError("target_fraction must be in (0, 1]")
        object.__setattr__(self, "goal_id", f"explore-galaxy:{self.target_fraction:.2f}")

    def _fraction(self, state: GameState) -> float:
        if not state.planets:
            return 1.0
        return sum(1 for p in state.planets if p.observed) / len(state.planets)

    def progress(self, state: GameState) -> float:
        return min(1.0, self._fraction(state) / self.target_fraction)

    def is_complete(self, state: GameState) -> bool:
        return self._fraction(state) >= self.target_fraction

    def apply(self, state, objectives, research, missions, planets) -> str:
        frac = self._fraction(state)
        if frac >= self.target_fraction:
            return f"{self.goal_id} complete ({frac:.1%})."
        objectives["scout"] = objectives.get("scout", 1.0) * (1.0 + 0.45 * self.priority)
        missions["scan"] = missions.get("scan", 1.0) * (1.0 + 0.50 * self.priority)
        research["electronics"] = research.get("electronics", 1.0) * (1.0 + 0.12 * self.priority)
        research["propulsion"] = research.get("propulsion", 1.0) * (1.0 + 0.12 * self.priority)
        return f"Goal {self.goal_id}: {frac:.1%}/{self.target_fraction:.0%}; prioritize scouting."


@dataclass(frozen=True)
class IndustrialCapacityGoal:
    target_factories: int
    target_mines: int
    priority: float = 1.0
    goal_id: str = "industrial-capacity"

    def __post_init__(self):
        object.__setattr__(self, "goal_id", f"industrial:{self.target_factories}f:{self.target_mines}m")

    def _totals(self, state: GameState) -> tuple[int, int]:
        owned = [p for p in state.planets if p.owner == state.player_id]
        return sum(p.factories for p in owned), sum(p.mines for p in owned)

    def progress(self, state: GameState) -> float:
        factories, mines = self._totals(state)
        f = min(1.0, factories / max(1, self.target_factories))
        m = min(1.0, mines / max(1, self.target_mines))
        return (f + m) / 2

    def is_complete(self, state: GameState) -> bool:
        factories, mines = self._totals(state)
        return factories >= self.target_factories and mines >= self.target_mines

    def apply(self, state, objectives, research, missions, planets) -> str:
        factories, mines = self._totals(state)
        if self.is_complete(state):
            return f"{self.goal_id} complete ({factories} factories, {mines} mines)."
        objectives["develop"] = objectives.get("develop", 1.0) * (1.0 + 0.35 * self.priority)
        planets["factories"] = planets.get("factories", 1.0) * (1.0 + (0.35 if factories < self.target_factories else 0.0) * self.priority)
        planets["mines"] = planets.get("mines", 1.0) * (1.0 + (0.35 if mines < self.target_mines else 0.0) * self.priority)
        research["construction"] = research.get("construction", 1.0) * (1.0 + 0.15 * self.priority)
        return f"Goal {self.goal_id}: {factories}/{self.target_factories} factories, {mines}/{self.target_mines} mines."
