from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Iterable

from .models import GameState
from ..util import distance
from ..goals import Goal
from ..colony_planner import colony_support_distance
from ..planet_economy import RESOURCE_TARGET, measure_empire_resources
from ..diplomacy import DiplomacyPolicy, allied_player_ids
from ..phase_strategy import StrategicPhase, StrategicPhaseManager, assign_planet_roles


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
    # Persist the macro controller's decision with the plan so all downstream
    # planners and playtest traces can explain *why* their priorities changed.
    phase: str = "unclassified"
    phase_reason: str = ""
    phase_assessment: dict[str, float | int] = field(default_factory=dict)
    planet_actions: dict[int, str] = field(default_factory=dict)

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

        # Apply the phase exactly once to each strategic dimension.  Several
        # consumers multiply an objective by a mission weight, so phase
        # multipliers belong on objectives (not both) to avoid squaring a
        # transition such as expansion or reconnaissance.
        phase_decision = StrategicPhaseManager().decide(state, self)
        phase_policy = phase_decision.policy
        objective_phase_weights = {
            Objective.SCOUT.value: phase_policy.explore_weight,
            Objective.EXPAND.value: phase_policy.expand_weight,
            Objective.DEVELOP.value: phase_policy.develop_weight,
            Objective.DEFEND.value: phase_policy.fortify_weight,
            Objective.ATTACK.value: phase_policy.attack_weight,
            Objective.RESEARCH.value: phase_policy.research_weight,
            Objective.LOGISTICS.value: max(
                0.65,
                (phase_policy.expand_weight * phase_policy.develop_weight) ** 0.5,
            ),
        }
        for objective, multiplier in objective_phase_weights.items():
            objectives[objective] = objectives.get(objective, 1.0) * multiplier
        for field_name in research:
            research[field_name] *= phase_policy.research_weight
        planets["factories"] = planets.get("factories", 1.0) * phase_policy.develop_weight
        planets["mines"] = planets.get("mines", 1.0) * phase_policy.develop_weight
        planets["defenses"] = planets.get("defenses", 1.0) * phase_policy.fortify_weight
        planets["ships"] = planets.get("ships", 1.0) * max(
            phase_policy.fortify_weight, phase_policy.attack_weight,
        )
        planets["research"] = planets.get("research", 1.0) * phase_policy.research_weight
        notes.append(
            f"STRATEGIC PHASE: {phase_decision.phase.value}; {phase_decision.reason}"
        )

        owned = [p for p in state.planets if p.owner == state.player_id]
        unknown = [p for p in state.planets if not p.observed]
        allies = allied_player_ids(state)
        hostiles = [
            f for f in state.fleets
            if f.owner != state.player_id and int(f.owner) not in allies
        ]
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
            # A nearby fleet used to flip the primary objective to defend while
            # dozens of unowned claims remained. During the opening and first
            # contact, expansion stays ahead of that defensive bump. Fortification
            # and later phases keep the phase weights unchanged.
            light_contact = (
                phase_decision.phase == StrategicPhase.FRONTIER_CONTACT
                and float(phase_decision.assessment.hostile_pressure) < 0.5
            )
            if phase_decision.phase == StrategicPhase.EARLY_EXPANSION or light_contact:
                defend_weight = objectives.get(Objective.DEFEND.value, 1.0)
                expand_weight = objectives.get(Objective.EXPAND.value, 1.0)
                floor = 1.45 if phase_decision.phase == StrategicPhase.EARLY_EXPANSION else 1.2
                if expand_weight < defend_weight * floor:
                    objectives[Objective.EXPAND.value] = defend_weight * floor
                    notes.append(
                        "Opening frontier remains; expansion stays ahead of the nearby-fleet defense bump."
                    )

        if unknown and not nearby_threats:
            objectives[Objective.SCOUT.value] = objectives.get(Objective.SCOUT.value, 1.0) * 1.10

        # A shared allied border is not a frontier. While supply range still
        # contains unowned worlds, keep planting and keep one scout program
        # alive even if a real hostile fleet is near home.
        open_claims = _open_supply_claims(state)
        if open_claims >= 6 and phase_decision.phase in {
            StrategicPhase.EARLY_EXPANSION, StrategicPhase.FRONTIER_CONTACT,
        }:
            growth_floor = max(
                objectives.get(Objective.DEFEND.value, 1.0),
                objectives.get(Objective.DEVELOP.value, 1.0),
            ) * 1.15
            if objectives.get(Objective.EXPAND.value, 1.0) < growth_floor:
                objectives[Objective.EXPAND.value] = growth_floor
                notes.append(
                    f"{open_claims} unowned worlds remain inside supply range; expansion stays ahead of defense and development."
                )
        if unknown:
            objectives[Objective.SCOUT.value] = max(objectives.get(Objective.SCOUT.value, 1.0), 1.05)

        resources = measure_empire_resources(state)
        remembered = dict(state.native.get("empire_resources") or {})
        resources["previous_year"] = remembered.get("previous_year")
        resources["previous_resources"] = remembered.get("previous_resources", remembered.get("resources"))
        resources["resource_delta"] = remembered.get("resource_delta")
        state.native["empire_resources"] = resources
        gap = int(resources["factory_gap_resources"])
        current_resources = int(resources["resources"])
        capped_resources = int(resources["resources_at_factory_cap"])
        if gap > max(20, int(current_resources * 0.15)):
            objectives[Objective.DEVELOP.value] = max(
                objectives.get(Objective.DEVELOP.value, 1.0),
                objectives.get(Objective.RESEARCH.value, 1.0),
            )
            notes.append(
                f"Population plus factories produce {current_resources:,} resources; "
                f"the factory cap would produce {capped_resources:,}. Factories stay ahead of research."
            )
        if capped_resources < RESOURCE_TARGET and open_claims >= 4:
            develop_weight = objectives.get(Objective.DEVELOP.value, 1.0)
            if objectives.get(Objective.EXPAND.value, 1.0) < develop_weight:
                objectives[Objective.EXPAND.value] = develop_weight
                notes.append(
                    f"Fully factored output would be {capped_resources:,}, under {RESOURCE_TARGET:,}. "
                    f"{open_claims} claims remain, so growth stays with defense."
                )
        saturated = (
            phase_decision.phase != StrategicPhase.EARLY_EXPANSION
            and float(phase_decision.assessment.expansion_saturation) >= 0.55
            and open_claims <= 3
            and len(owned) >= 8
        )
        if saturated:
            objectives[Objective.ATTACK.value] = max(
                objectives.get(Objective.ATTACK.value, 1.0),
                objectives.get(Objective.DEFEND.value, 1.0),
            )
            notes.append(
                "The nearby frontier is saturated. Capture high-value enemy worlds and raid the rest."
            )

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

        planet_actions = assign_planet_roles(
            state,
            full=phase_decision.phase != StrategicPhase.EARLY_EXPANSION,
        ) if phase_decision.phase in {
            StrategicPhase.FRONTIER_CONTACT,
            StrategicPhase.MIDGAME,
            StrategicPhase.INDUSTRIAL_BUILDOUT,
            StrategicPhase.FORTIFICATION,
            StrategicPhase.OPPORTUNISTIC_WAR,
        } else {}
        if planet_actions:
            notes.append(
                "Planet jobs: "
                + ", ".join(f"{role}={sum(1 for item in planet_actions.values() if item == role)}"
                            for role in ("hold_line", "industrialize", "ships", "tech")
                            if any(item == role for item in planet_actions.values()))
            )

        from ..empire_optimizer import classify_planet_role

        classified = [
            classify_planet_role(planet, state)
            for planet in getattr(state, "planets", []) or []
            if getattr(planet, "owner", None) == int(getattr(state, "player_id", 0) or 0)
        ]
        if classified:
            def _count(field: str, value: str) -> int:
                return sum(1 for item in classified if getattr(item, field) == value)

            jobs = ", ".join(
                f"{name.lower()}={_count('role', name)}"
                for name in ("GROWTH", "FORTIFY", "INDUSTRIAL", "OUTPOST", "SKIP")
                if _count("role", name)
            )
            stages = ", ".join(
                f"{label}={_count('development_band', name)}"
                for name, label in (
                    ("COLONY", "colony"),
                    ("SOLID_POP", "solid_pop"),
                    ("DEVELOPED", "developed"),
                )
                if _count("development_band", name)
            )
            notes.append(f"Planet roles: {jobs}; {stages}")

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
            phase=phase_decision.phase.value,
            phase_reason=phase_decision.reason,
            phase_assessment={
                "explored_fraction": phase_decision.assessment.explored_fraction,
                "owned_planets": phase_decision.assessment.owned_planets,
                "known_enemy_players": phase_decision.assessment.known_enemy_players,
                "contested_frontier_fraction": phase_decision.assessment.contested_frontier_fraction,
                "open_frontier_fraction": phase_decision.assessment.open_frontier_fraction,
                "frontier_pressure": phase_decision.assessment.frontier_pressure,
                "expansion_saturation": phase_decision.assessment.expansion_saturation,
                "hostile_worlds": phase_decision.assessment.hostile_worlds,
                "hostile_pressure": phase_decision.assessment.hostile_pressure,
            },
            planet_actions=planet_actions,
        )


    def with_human_players(self, *player_ids: int) -> "StrategicPersona":
        """Declare player slots controlled by humans.

        An unconfigured human is never offered an alliance. A human listed in
        the game's allied group stays allied and is not attacked.
        """
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


def _open_supply_claims(state: GameState) -> int:
    """Unowned worlds close enough that a colony ship can still be supplied."""

    owned = [planet for planet in state.planets if planet.owner == state.player_id]
    if not owned:
        return 0
    limit = colony_support_distance(state)
    count = 0
    for planet in state.planets:
        if planet.owner is not None:
            continue
        nearest = min(distance(planet.position, owned_planet.position) for owned_planet in owned)
        if nearest > limit:
            continue
        if (not planet.observed) or int(planet.habitability or 0) > 0:
            count += 1
    return count


def persona_from_name(name: str | None) -> StrategicPersona:
    key = (name or "balanced").strip().lower()
    try:
        return PERSONAS[key]()
    except KeyError as exc:
        raise ValueError(f"Unknown persona {name!r}; choose from {', '.join(sorted(PERSONAS))}") from exc


# --- Phase-strategy defaults (v2.4) ---
# These are class-level defaults so existing persona constructors remain backward compatible.
try:
    StrategicPersona.opening_min_planets = 8
    StrategicPersona.midgame_planets = 12
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
