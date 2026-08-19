from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Iterable

from .models import GameState
from .util import distance
from .goals import Goal
from .diplomacy import DiplomacyPolicy


class Objective(str, Enum):
    EXPAND = "expand"
    DEVELOP = "develop"
    RESEARCH = "research"
    DEFEND = "defend"
    ATTACK = "attack"
    SCOUT = "scout"
    LOGISTICS = "logistics"


@dataclass(frozen=True)
class StrategicPlan:
    """Macro-level guidance produced by a persona for one turn.

    Strategy modules consume this object; the persona itself does not emit native
    Stars! orders. That keeps personality/goal selection separate from mechanics.
    """

    persona_name: str
    objective_weights: dict[str, float]
    research_weights: dict[str, float]
    fleet_mission_weights: dict[str, float]
    planet_weights: dict[str, float]
    risk_tolerance: float = 0.5
    colonize_min_habitability: int = 40
    scout_aggressiveness: float = 0.5
    defense_radius: float = 100.0
    attack_strength_ratio: float = 1.25
    notes: tuple[str, ...] = ()
    goal_progress: dict[str, float] = field(default_factory=dict)
    diplomacy: dict[int, dict[str, Any]] = field(default_factory=dict)

    def objective(self, name: Objective | str, default: float = 1.0) -> float:
        key = name.value if isinstance(name, Objective) else str(name)
        return float(self.objective_weights.get(key, default))

    def research(self, field: str, default: float = 1.0) -> float:
        return float(self.research_weights.get(field, default))

    def mission(self, name: str, default: float = 1.0) -> float:
        return float(self.fleet_mission_weights.get(name, default))

    def planet(self, name: str, default: float = 1.0) -> float:
        return float(self.planet_weights.get(name, default))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StrategicPersona:
    name: str = "Balanced"
    objective_weights: dict[str, float] = field(default_factory=lambda: {
        Objective.EXPAND.value: 1.0,
        Objective.DEVELOP.value: 1.0,
        Objective.RESEARCH.value: 1.0,
        Objective.DEFEND.value: 1.0,
        Objective.ATTACK.value: 0.8,
        Objective.SCOUT.value: 1.0,
        Objective.LOGISTICS.value: 1.0,
    })
    research_weights: dict[str, float] = field(default_factory=lambda: {
        "energy": 1.0,
        "weapons": 1.0,
        "propulsion": 1.0,
        "construction": 1.0,
        "electronics": 1.0,
        "biotechnology": 1.0,
    })
    fleet_mission_weights: dict[str, float] = field(default_factory=lambda: {
        "scan": 1.0,
        "colonize": 1.0,
        "defend": 1.0,
        "attack": 0.8,
        "transport": 1.0,
        "mine": 1.0,
    })
    planet_weights: dict[str, float] = field(default_factory=lambda: {
        "factories": 1.0,
        "mines": 1.0,
        "defenses": 1.0,
        "ships": 1.0,
        "research": 1.0,
    })
    risk_tolerance: float = 0.5
    colonize_min_habitability: int = 40
    scout_aggressiveness: float = 0.5
    defense_radius: float = 100.0
    attack_strength_ratio: float = 1.25
    goals: tuple[Goal, ...] = ()
    diplomacy_policy: DiplomacyPolicy = field(default_factory=DiplomacyPolicy)

    def build_plan(self, state: GameState) -> StrategicPlan:
        """Produce a plan adjusted for the current strategic situation."""
        objectives = dict(self.objective_weights)
        research = dict(self.research_weights)
        missions = dict(self.fleet_mission_weights)
        planets = dict(self.planet_weights)
        notes: list[str] = []
        goal_progress: dict[str, float] = {}
        diplomacy_views = self.diplomacy_policy.evaluate_all(state)
        diplomacy = {pid: view.to_dict() for pid, view in diplomacy_views.items()}

        owned = [p for p in state.planets if p.owner == state.player_id]
        unknown = [p for p in state.planets if not p.observed]
        hostiles = [f for f in state.fleets if f.owner != state.player_id]
        combat = [f for f in state.fleets if f.owner == state.player_id and f.role == "combat"]
        colony = [f for f in state.fleets if f.owner == state.player_id and f.role == "colony"]

        # Situation-aware adjustments are deliberately small; the persona remains
        # recognizable while still reacting to the game.
        nearby_threats = 0
        if owned:
            for enemy in hostiles:
                if min(distance(enemy.position, p.position) for p in owned) <= self.defense_radius:
                    nearby_threats += 1

        if nearby_threats:
            objectives[Objective.DEFEND.value] = objectives.get(Objective.DEFEND.value, 1.0) * (1.0 + min(1.0, 0.25 * nearby_threats))
            research["weapons"] = research.get("weapons", 1.0) * 1.20
            research["energy"] = research.get("energy", 1.0) * 1.10
            planets["defenses"] = planets.get("defenses", 1.0) * 1.25
            missions["defend"] = missions.get("defend", 1.0) * 1.30
            notes.append(f"Detected {nearby_threats} nearby hostile fleet(s); increased defense posture.")

        if unknown and not nearby_threats:
            objectives[Objective.SCOUT.value] = objectives.get(Objective.SCOUT.value, 1.0) * 1.10

        if colony and unknown:
            research["propulsion"] = research.get("propulsion", 1.0) * 1.08

        if len(owned) <= 2:
            objectives[Objective.EXPAND.value] = objectives.get(Objective.EXPAND.value, 1.0) * 1.10
            missions["colonize"] = missions.get("colonize", 1.0) * 1.10

        if combat and not hostiles:
            objectives[Objective.ATTACK.value] = objectives.get(Objective.ATTACK.value, 0.8) * 0.85

        for goal in self.goals:
            goal_progress[goal.goal_id] = goal.progress(state)
            notes.append(goal.apply(state, objectives, research, missions, planets))

        return StrategicPlan(
            persona_name=self.name,
            objective_weights=objectives,
            research_weights=research,
            fleet_mission_weights=missions,
            planet_weights=planets,
            risk_tolerance=self.risk_tolerance,
            colonize_min_habitability=self.colonize_min_habitability,
            scout_aggressiveness=self.scout_aggressiveness,
            defense_radius=self.defense_radius,
            attack_strength_ratio=self.attack_strength_ratio,
            notes=tuple(notes),
            goal_progress=goal_progress,
            diplomacy=diplomacy,
        )


    def with_human_players(self, *player_ids: int) -> "StrategicPersona":
        """Declare player slots controlled by humans. Humans can never be allied."""
        self.diplomacy_policy.human_player_ids = frozenset(int(x) for x in player_ids)
        return self

    def with_goals(self, *goals: Goal) -> "StrategicPersona":
        self.goals = tuple(goals)
        return self


class BalancedPersona(StrategicPersona):
    def __init__(self):
        super().__init__(name="Balanced")


class ExpansionistPersona(StrategicPersona):
    def __init__(self):
        super().__init__(
            name="Expansionist",
            objective_weights={
                "expand": 1.55, "develop": 0.95, "research": 1.0,
                "defend": 0.85, "attack": 0.65, "scout": 1.45, "logistics": 1.25,
            },
            research_weights={
                "energy": 0.85, "weapons": 0.70, "propulsion": 1.60,
                "construction": 1.20, "electronics": 1.25, "biotechnology": 1.20,
            },
            fleet_mission_weights={
                "scan": 1.55, "colonize": 1.70, "defend": 0.85,
                "attack": 0.60, "transport": 1.35, "mine": 1.0,
            },
            planet_weights={
                "factories": 1.10, "mines": 0.95, "defenses": 0.65,
                "ships": 1.10, "research": 0.90,
            },
            risk_tolerance=0.65,
            colonize_min_habitability=30,
            scout_aggressiveness=0.85,
            defense_radius=80.0,
            attack_strength_ratio=1.45,
        )


class IndustrialistPersona(StrategicPersona):
    def __init__(self):
        super().__init__(
            name="Industrialist",
            objective_weights={
                "expand": 0.95, "develop": 1.65, "research": 1.0,
                "defend": 1.05, "attack": 0.65, "scout": 0.85, "logistics": 1.25,
            },
            research_weights={
                "energy": 1.05, "weapons": 0.75, "propulsion": 0.95,
                "construction": 1.50, "electronics": 0.90, "biotechnology": 1.05,
            },
            fleet_mission_weights={
                "scan": 0.85, "colonize": 1.0, "defend": 1.05,
                "attack": 0.60, "transport": 1.45, "mine": 1.45,
            },
            planet_weights={
                "factories": 1.65, "mines": 1.50, "defenses": 1.0,
                "ships": 0.90, "research": 1.0,
            },
            risk_tolerance=0.35,
            colonize_min_habitability=40,
            scout_aggressiveness=0.35,
            defense_radius=105.0,
            attack_strength_ratio=1.50,
        )


class TechnologistPersona(StrategicPersona):
    def __init__(self):
        super().__init__(
            name="Technologist",
            objective_weights={
                "expand": 0.90, "develop": 1.0, "research": 1.75,
                "defend": 0.95, "attack": 0.70, "scout": 1.0, "logistics": 0.9,
            },
            research_weights={
                "energy": 1.20, "weapons": 1.10, "propulsion": 1.20,
                "construction": 1.20, "electronics": 1.65, "biotechnology": 1.20,
            },
            fleet_mission_weights={
                "scan": 1.05, "colonize": 0.90, "defend": 0.95,
                "attack": 0.70, "transport": 0.90, "mine": 0.85,
            },
            planet_weights={
                "factories": 1.0, "mines": 0.90, "defenses": 0.85,
                "ships": 0.85, "research": 1.65,
            },
            risk_tolerance=0.40,
            colonize_min_habitability=45,
            scout_aggressiveness=0.55,
            defense_radius=100.0,
            attack_strength_ratio=1.35,
        )


class MilitaristPersona(StrategicPersona):
    def __init__(self):
        super().__init__(
            name="Militarist",
            objective_weights={
                "expand": 0.85, "develop": 0.90, "research": 1.0,
                "defend": 1.25, "attack": 1.70, "scout": 0.90, "logistics": 0.85,
            },
            research_weights={
                "energy": 1.30, "weapons": 1.70, "propulsion": 1.25,
                "construction": 1.40, "electronics": 1.10, "biotechnology": 0.65,
            },
            fleet_mission_weights={
                "scan": 0.85, "colonize": 0.80, "defend": 1.35,
                "attack": 1.75, "transport": 0.75, "mine": 0.70,
            },
            planet_weights={
                "factories": 1.0, "mines": 1.05, "defenses": 1.35,
                "ships": 1.55, "research": 0.90,
            },
            risk_tolerance=0.75,
            colonize_min_habitability=45,
            scout_aggressiveness=0.60,
            defense_radius=130.0,
            attack_strength_ratio=0.95,
        )


PERSONAS = {
    "balanced": BalancedPersona,
    "expansionist": ExpansionistPersona,
    "industrialist": IndustrialistPersona,
    "technologist": TechnologistPersona,
    "militarist": MilitaristPersona,
}


def persona_from_name(name: str | None) -> StrategicPersona:
    key = (name or "balanced").strip().lower()
    try:
        return PERSONAS[key]()
    except KeyError as exc:
        raise ValueError(f"Unknown persona {name!r}; choose from {', '.join(sorted(PERSONAS))}") from exc


# --- Phase-strategy defaults (v2.4) ---
# These are class-level defaults so existing persona constructors remain backward compatible.
try:
    StrategicPersona.opening_min_planets = 4
    StrategicPersona.frontier_contact_threshold = 0.18
    StrategicPersona.expansion_saturation_threshold = 0.55
    StrategicPersona.transition_style = "consolidate"
    StrategicPersona.planet_selectivity = 0.35
except NameError:
    pass

for _name, _style, _selectivity in (
    ("ExpansionistPersona", "consolidate", 0.20),
    ("IndustrialistPersona", "industry", 0.45),
    ("TechnologistPersona", "technology", 0.55),
    ("MilitaristPersona", "attack", 0.25),
    ("BalancedPersona", "fortify", 0.40),
):
    _cls = globals().get(_name)
    if _cls is not None:
        _cls.transition_style = _style
        _cls.planet_selectivity = _selectivity
