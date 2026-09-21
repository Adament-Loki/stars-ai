"""Typed state contracts for the public AI facade.

``GameState`` remains the observed, normalized Stars! input.  The classes in
this module deliberately keep two other lifetimes separate from it:

* ``PersistentAIState`` is an adapter over the on-disk-compatible
  :class:`~stars_ai.memory.AgentMemory` shape.
* ``TurnAIState`` is a detached snapshot of reasoning produced during one
  planning call.

The Phase 2 facade still delegates to ``StarsAgent``.  Its legacy planners and
native report adapter therefore continue to use their existing
``GameState.native`` compatibility entries.  New facade callers use the typed
turn state instead; Phase 3 can migrate individual consumers only after their
behavioral contracts are covered.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from ..memory import AgentMemory
from ..models import GameState
from ..persona import StrategicPersona


@dataclass(slots=True)
class TurnInput:
    """Information required to plan one player's normalized game turn.

    ``state`` and ``memory`` deliberately remain the existing project types.
    The facade does not copy, translate, or reinterpret them: that keeps the
    current mutation and persistence semantics identical to ``StarsAgent``.
    ``player_id`` is an explicit public identity field, although Phase 1 does
    not add validation beyond the legacy agent's existing behavior.
    """

    state: GameState
    player_id: int
    memory: AgentMemory | None = None
    persona: StrategicPersona | None = None


# Persistent state ---------------------------------------------------------
#
# The nested records give the otherwise flat AgentMemory fields clear ownership
# without changing the persisted JSON layout.  Fields whose contents are
# deliberately extensible (for example raw planet intelligence records) retain
# their established mapping shapes instead of inventing a lossy schema.


@dataclass(slots=True)
class PersistentIdentityState:
    """Identity and lifecycle metadata for one player's remembered game."""

    schema_version: int = 8
    game_id: int | None = None
    player_id: int | None = None
    start_year: int | None = None
    last_year: int | None = None
    initial_owned_planet_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class PersistentIntelligenceState:
    """Observed-world and opponent knowledge that survives between turns."""

    known_enemy_planets: dict[str, int] = field(default_factory=dict)
    planet_scores: dict[str, float] = field(default_factory=dict)
    planet_intel: dict[str, dict[str, Any]] = field(default_factory=dict)
    colonized_years: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class PersistentExplorationState:
    """Route history used to continue and deconflict exploration."""

    scan_target_history: dict[str, dict[str, Any]] = field(default_factory=dict)
    scout_routes: dict[str, dict[str, Any]] = field(default_factory=dict)
    fleet_movement_history: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(slots=True)
class PersistentResearchState:
    """Research continuity and completed-sprint history."""

    state: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class PersistentDiplomacyState:
    """The last strategic assessment for each contacted player."""

    relations: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(slots=True)
class PersistentActionLedgerState:
    """Native command expectations, outcomes, and capability read-backs."""

    expectations: dict[str, dict[str, Any]] = field(default_factory=dict)
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    capability_execution: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(slots=True)
class PersistentHistoryState:
    """Longer-lived strategic notes, KPI history, and defensive pressure."""

    strategic_notes: list[str] = field(default_factory=list)
    goal_progress: dict[str, float] = field(default_factory=dict)
    kpi_history: list[dict[str, Any]] = field(default_factory=list)
    territorial_exposure_history: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PersistentAIState:
    """Typed view of all memory that survives between AI turns.

    Conversion is intentionally exact for every current ``AgentMemory`` field.
    It neither alters the legacy memory object nor changes its JSON schema.
    """

    identity: PersistentIdentityState = field(default_factory=PersistentIdentityState)
    intelligence: PersistentIntelligenceState = field(default_factory=PersistentIntelligenceState)
    exploration: PersistentExplorationState = field(default_factory=PersistentExplorationState)
    research: PersistentResearchState = field(default_factory=PersistentResearchState)
    diplomacy: PersistentDiplomacyState = field(default_factory=PersistentDiplomacyState)
    action_ledger: PersistentActionLedgerState = field(default_factory=PersistentActionLedgerState)
    history: PersistentHistoryState = field(default_factory=PersistentHistoryState)
    # Opaque future/legacy JSON fields are retained by the persistence adapter
    # but deliberately do not become strategic inputs until modeled here.
    legacy_extra_fields: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_agent_memory(cls, memory: AgentMemory) -> "PersistentAIState":
        """Create a detached typed view without mutating legacy memory."""

        return cls(
            identity=PersistentIdentityState(
                schema_version=int(memory.schema_version),
                game_id=memory.game_id,
                player_id=memory.player_id,
                start_year=memory.start_year,
                last_year=memory.last_year,
                initial_owned_planet_ids=deepcopy(memory.initial_owned_planet_ids),
            ),
            intelligence=PersistentIntelligenceState(
                known_enemy_planets=deepcopy(memory.known_enemy_planets),
                planet_scores=deepcopy(memory.planet_scores),
                planet_intel=deepcopy(memory.planet_intel),
                colonized_years=deepcopy(memory.colonized_years),
            ),
            exploration=PersistentExplorationState(
                scan_target_history=deepcopy(memory.scan_target_history),
                scout_routes=deepcopy(memory.scout_routes),
                fleet_movement_history=deepcopy(memory.fleet_movement_history),
            ),
            research=PersistentResearchState(
                state=deepcopy(memory.research_state),
                history=deepcopy(memory.research_history),
            ),
            diplomacy=PersistentDiplomacyState(relations=deepcopy(memory.diplomacy)),
            action_ledger=PersistentActionLedgerState(
                expectations=deepcopy(memory.action_expectations),
                outcomes=deepcopy(memory.action_outcome_history),
                capability_execution=deepcopy(memory.capability_execution),
            ),
            history=PersistentHistoryState(
                strategic_notes=deepcopy(memory.strategic_notes),
                goal_progress=deepcopy(memory.goal_progress),
                kpi_history=deepcopy(memory.kpi_history),
                territorial_exposure_history=deepcopy(memory.territorial_exposure_history),
            ),
            legacy_extra_fields=deepcopy(
                getattr(memory, "_legacy_extra_fields", {})
            ),
        )

    def apply_to_agent_memory(self, memory: AgentMemory) -> None:
        """Apply this view using the existing memory object's JSON-compatible fields."""

        memory.schema_version = int(self.identity.schema_version)
        memory.game_id = self.identity.game_id
        memory.player_id = self.identity.player_id
        memory.start_year = self.identity.start_year
        memory.last_year = self.identity.last_year
        memory.initial_owned_planet_ids = deepcopy(self.identity.initial_owned_planet_ids)

        memory.known_enemy_planets = deepcopy(self.intelligence.known_enemy_planets)
        memory.planet_scores = deepcopy(self.intelligence.planet_scores)
        memory.planet_intel = deepcopy(self.intelligence.planet_intel)
        memory.colonized_years = deepcopy(self.intelligence.colonized_years)

        memory.scan_target_history = deepcopy(self.exploration.scan_target_history)
        memory.scout_routes = deepcopy(self.exploration.scout_routes)
        memory.fleet_movement_history = deepcopy(self.exploration.fleet_movement_history)

        memory.research_state = deepcopy(self.research.state)
        memory.research_history = deepcopy(self.research.history)
        memory.diplomacy = deepcopy(self.diplomacy.relations)

        memory.action_expectations = deepcopy(self.action_ledger.expectations)
        memory.action_outcome_history = deepcopy(self.action_ledger.outcomes)
        memory.capability_execution = deepcopy(self.action_ledger.capability_execution)

        memory.strategic_notes = deepcopy(self.history.strategic_notes)
        memory.goal_progress = deepcopy(self.history.goal_progress)
        memory.kpi_history = deepcopy(self.history.kpi_history)
        memory.territorial_exposure_history = deepcopy(self.history.territorial_exposure_history)
        memory._legacy_extra_fields = deepcopy(self.legacy_extra_fields)

    def to_agent_memory(self) -> AgentMemory:
        """Return a new legacy-memory object for persistence compatibility."""

        memory = AgentMemory()
        self.apply_to_agent_memory(memory)
        return memory


# Current-turn state -------------------------------------------------------


@dataclass(slots=True)
class StrategicPhaseState:
    """Persona phase selected for this planning cycle."""

    phase: str
    reason: str
    assessment: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StrategicWatchdogState:
    """Typed high-level expansion watchdog values used this turn."""

    year: int
    turn: int
    total_planets: int
    explored_count: int
    explored_percent: float
    new_colonies: int
    owned_planets: int
    exploration_pressure: float
    colonization_pressure: float
    milestone: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResearchDecisionState:
    """Serializable research decision, including execution-contract evidence."""

    capability_id: str
    capability_name: str
    category: str
    posture: str
    current_field: str
    next_field: str
    allocation_percent: int
    estimated_turns: int
    reason: str
    commitment: str = ""
    execution_status: str = "ACTIVE"
    expected_resources: int = 0
    sprint_executable: bool = True
    sprint_blocked_reason: str | None = None
    execution_failure_reason: str | None = None
    requirements: dict[str, int] = field(default_factory=dict)
    remaining_requirements: dict[str, int] = field(default_factory=dict)
    contributor_planet_ids: list[int] = field(default_factory=list)
    protected_production_planet_ids: list[int] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TerritorialDefenseState:
    """Current territorial assessment preserved outside the native scratch map."""

    needs_response: bool
    needs_minefield: bool
    desired_patrols: int
    desired_minelayers: int
    reason: str
    violations: list[dict[str, Any]] = field(default_factory=list)
    uncovered_minefield_anchor_ids: list[int] = field(default_factory=list)
    exposure: dict[str, Any] = field(default_factory=dict)
    source_escalation: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CommandOutcomeState:
    """A prior command's status as evaluated at the start of this turn."""

    status: str
    message: str
    action_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MovementProgressState:
    """A fleet movement diagnostic, detached from decoder/native metadata."""

    fleet_id: int
    destination_planet_id: int | None
    actual_progress: float | None
    flag: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BlockedActionState:
    """A capability or operation that cannot safely execute this turn."""

    action_id: str
    status: str
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SafetyDiagnosticState:
    """One waypoint or route-safety finding from the legacy compatibility path."""

    source: str
    fleet_id: int | None
    status: str
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FleetIntentState:
    """The concrete objective, continuation, hold, or block for one fleet."""

    fleet_id: int
    fleet_name: str
    role: str
    action: str
    reason: str
    destination_planet_id: int | None = None
    colony_candidates: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TurnAIState:
    """Reasoning and diagnostics whose lifetime ends with one planning turn.

    ``from_legacy_native`` is deliberately a one-way compatibility adapter.
    It copies the known strategic scratch entries after the unchanged legacy
    coordinator has finished.  No native/raw metadata is removed or rewritten.
    """

    strategic_phase: StrategicPhaseState | None = None
    strategic_watchdog: StrategicWatchdogState | None = None
    research_decision: ResearchDecisionState | None = None
    territorial_defense: TerritorialDefenseState | None = None
    command_outcomes: list[CommandOutcomeState] = field(default_factory=list)
    movement_progress_diagnostics: list[MovementProgressState] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocked_actions: list[BlockedActionState] = field(default_factory=list)
    safety_diagnostics: list[SafetyDiagnosticState] = field(default_factory=list)
    fleet_intents: list[FleetIntentState] = field(default_factory=list)

    @classmethod
    def from_legacy_native(
        cls,
        native: dict[str, Any] | None,
        *,
        fleet_intents: list[dict[str, Any]] | None = None,
    ) -> "TurnAIState":
        """Capture established planner scratch values without sharing mutable data."""

        values = native or {}
        state = cls(fleet_intents=_fleet_intents_from_payload(fleet_intents))
        state.strategic_phase = _strategic_phase_from_payload(values.get("strategic_phase"))
        state.strategic_watchdog = _watchdog_from_payload(values.get("strategic_watchdog"))
        state.research_decision = _research_from_payload(values.get("research_strategy"))
        state.territorial_defense = _territorial_from_payload(values.get("territorial_defense"))
        state.command_outcomes = _command_outcomes_from_payload(values.get("command_outcomes"))
        state.movement_progress_diagnostics = _movement_from_payload(
            values.get("movement_progress_diagnostics")
        )
        state.warnings = [
            outcome.message
            for outcome in state.command_outcomes
            if outcome.status in {"WARNING", "UNVERIFIED"}
        ]
        capability_diagnostics = values.get("capability_execution_diagnostics") or {}
        if isinstance(capability_diagnostics, dict):
            state.blocked_actions = [
                BlockedActionState(
                    action_id=str(capability_id),
                    status="BLOCKED_NATIVE",
                )
                for capability_id in capability_diagnostics.get("blocked_capability_ids", []) or []
            ]
        elif isinstance(capability_diagnostics, list):
            state.blocked_actions = _blocked_actions_from_payload(capability_diagnostics)
        state.safety_diagnostics = _safety_diagnostics_from_payload(
            values.get("waypoint_diagnostics"), source="waypoint"
        ) + _safety_diagnostics_from_payload(
            values.get("probe_route_diagnostics"), source="probe_route"
        )
        return state


@dataclass(slots=True)
class AIState:
    """One public turn's observed game, durable memory, and current reasoning."""

    game: GameState
    persistent: PersistentAIState
    turn: TurnAIState = field(default_factory=TurnAIState)

    @classmethod
    def from_agent_memory(cls, game: GameState, memory: AgentMemory) -> "AIState":
        """Build state for a facade call without changing either legacy input."""

        return cls(game=game, persistent=PersistentAIState.from_agent_memory(memory))


def _payload(value: object) -> dict[str, Any] | None:
    return deepcopy(value) if isinstance(value, dict) else None


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _strategic_phase_from_payload(value: object) -> StrategicPhaseState | None:
    payload = _payload(value)
    if payload is None:
        return None
    return StrategicPhaseState(
        phase=str(payload.get("phase") or ""),
        reason=str(payload.get("reason") or ""),
        assessment=deepcopy(dict(payload.get("assessment") or {})),
    )


def _watchdog_from_payload(value: object) -> StrategicWatchdogState | None:
    payload = _payload(value)
    if payload is None:
        return None
    typed_keys = {
        "year", "turn", "total_planets", "explored_count", "explored_percent",
        "new_colonies", "owned_planets", "exploration_pressure",
        "colonization_pressure", "milestone", "notes",
    }
    return StrategicWatchdogState(
        year=_as_int(payload.get("year")),
        turn=_as_int(payload.get("turn")),
        total_planets=_as_int(payload.get("total_planets")),
        explored_count=_as_int(payload.get("explored_count")),
        explored_percent=_as_float(payload.get("explored_percent")),
        new_colonies=_as_int(payload.get("new_colonies")),
        owned_planets=_as_int(payload.get("owned_planets")),
        exploration_pressure=_as_float(payload.get("exploration_pressure"), 1.0),
        colonization_pressure=_as_float(payload.get("colonization_pressure"), 1.0),
        milestone=deepcopy(dict(payload.get("milestone") or {})),
        notes=[str(note) for note in (payload.get("notes") or [])],
        details={key: deepcopy(item) for key, item in payload.items() if key not in typed_keys},
    )


def _research_from_payload(value: object) -> ResearchDecisionState | None:
    payload = _payload(value)
    if payload is None:
        return None
    typed_keys = {
        "capability_id", "capability_name", "category", "posture", "current_field",
        "next_field", "allocation_percent", "estimated_turns", "reason", "commitment",
        "execution_status", "expected_resources", "sprint_executable", "sprint_blocked_reason",
        "execution_failure_reason", "requirements", "remaining_requirements",
        "contributor_planet_ids", "protected_production_planet_ids",
    }
    return ResearchDecisionState(
        capability_id=str(payload.get("capability_id") or ""),
        capability_name=str(payload.get("capability_name") or ""),
        category=str(payload.get("category") or ""),
        posture=str(payload.get("posture") or ""),
        current_field=str(payload.get("current_field") or payload.get("field") or ""),
        next_field=str(payload.get("next_field") or ""),
        allocation_percent=_as_int(payload.get("allocation_percent")),
        estimated_turns=_as_int(payload.get("estimated_turns")),
        reason=str(payload.get("reason") or ""),
        commitment=str(payload.get("commitment") or ""),
        execution_status=str(payload.get("execution_status") or "ACTIVE"),
        expected_resources=_as_int(payload.get("expected_resources")),
        sprint_executable=bool(payload.get("sprint_executable", True)),
        sprint_blocked_reason=(
            str(payload["sprint_blocked_reason"])
            if payload.get("sprint_blocked_reason") is not None else None
        ),
        execution_failure_reason=(
            str(payload["execution_failure_reason"])
            if payload.get("execution_failure_reason") is not None else None
        ),
        requirements={str(key): _as_int(item) for key, item in dict(payload.get("requirements") or {}).items()},
        remaining_requirements={
            str(key): _as_int(item)
            for key, item in dict(payload.get("remaining_requirements") or {}).items()
        },
        contributor_planet_ids=[_as_int(item) for item in (payload.get("contributor_planet_ids") or [])],
        protected_production_planet_ids=[
            _as_int(item) for item in (payload.get("protected_production_planet_ids") or [])
        ],
        details={key: deepcopy(item) for key, item in payload.items() if key not in typed_keys},
    )


def _territorial_from_payload(value: object) -> TerritorialDefenseState | None:
    payload = _payload(value)
    if payload is None:
        return None
    typed_keys = {
        "violations", "uncovered_minefield_anchor_ids", "desired_patrols",
        "desired_minelayers", "reason", "exposure", "source_escalation",
    }
    violations = [deepcopy(item) for item in (payload.get("violations") or []) if isinstance(item, dict)]
    anchors = [_as_int(item) for item in (payload.get("uncovered_minefield_anchor_ids") or [])]
    return TerritorialDefenseState(
        needs_response=bool(violations),
        needs_minefield=bool(anchors),
        desired_patrols=_as_int(payload.get("desired_patrols")),
        desired_minelayers=_as_int(payload.get("desired_minelayers")),
        reason=str(payload.get("reason") or ""),
        violations=violations,
        uncovered_minefield_anchor_ids=anchors,
        exposure=deepcopy(dict(payload.get("exposure") or {})),
        source_escalation=deepcopy(dict(payload.get("source_escalation") or {})),
        details={key: deepcopy(item) for key, item in payload.items() if key not in typed_keys},
    )


def _command_outcomes_from_payload(value: object) -> list[CommandOutcomeState]:
    if not isinstance(value, list):
        return []
    outcomes = []
    for item in value:
        payload = _payload(item)
        if payload is None:
            continue
        action_id = payload.pop("action_id", None)
        outcomes.append(CommandOutcomeState(
            status=str(payload.pop("status", "")),
            message=str(payload.pop("message", "")),
            action_id=str(action_id) if action_id is not None else None,
            details=payload,
        ))
    return outcomes


def _movement_from_payload(value: object) -> list[MovementProgressState]:
    if not isinstance(value, list):
        return []
    diagnostics = []
    for item in value:
        payload = _payload(item)
        if payload is None:
            continue
        fleet_id = _as_int(payload.pop("fleet_id", 0))
        destination = payload.pop("destination_planet_id", None)
        actual = payload.pop("actual_progress", None)
        flag = payload.pop("flag", None)
        diagnostics.append(MovementProgressState(
            fleet_id=fleet_id,
            destination_planet_id=_as_int(destination) if destination is not None else None,
            actual_progress=_as_float(actual) if actual is not None else None,
            flag=str(flag) if flag is not None else None,
            details=payload,
        ))
    return diagnostics


def _blocked_actions_from_payload(value: object) -> list[BlockedActionState]:
    if not isinstance(value, list):
        return []
    blocked = []
    for item in value:
        payload = _payload(item)
        if payload is None or not str(payload.get("status", "")).startswith("BLOCKED"):
            continue
        action_id = payload.pop("action_id", payload.pop("capability_id", ""))
        status = payload.pop("status", "BLOCKED")
        reason = payload.pop("reason", payload.pop("blocking_reason", None))
        blocked.append(BlockedActionState(
            action_id=str(action_id),
            status=str(status),
            reason=str(reason) if reason is not None else None,
            details=payload,
        ))
    return blocked


def _safety_diagnostics_from_payload(
    value: object,
    *,
    source: str,
) -> list[SafetyDiagnosticState]:
    if not isinstance(value, list):
        return []
    diagnostics = []
    for item in value:
        payload = _payload(item)
        if payload is None:
            continue
        fleet_id = payload.pop("fleet_id", None)
        status = payload.pop("status", payload.pop("result", ""))
        reason = payload.pop("reason", None)
        diagnostics.append(SafetyDiagnosticState(
            source=source,
            fleet_id=_as_int(fleet_id) if fleet_id is not None else None,
            status=str(status),
            reason=str(reason) if reason is not None else None,
            details=payload,
        ))
    return diagnostics


def _fleet_intents_from_payload(value: object) -> list[FleetIntentState]:
    if not isinstance(value, list):
        return []
    intents = []
    for item in value:
        payload = _payload(item)
        if payload is None:
            continue
        destination = payload.pop("destination_planet_id", None)
        candidates = payload.pop("colony_candidates", [])
        intents.append(FleetIntentState(
            fleet_id=_as_int(payload.pop("fleet_id", 0)),
            fleet_name=str(payload.pop("fleet_name", "")),
            role=str(payload.pop("role", "unknown")),
            action=str(payload.pop("action", "")),
            reason=str(payload.pop("reason", "")),
            destination_planet_id=_as_int(destination) if destination is not None else None,
            colony_candidates=[
                deepcopy(candidate) for candidate in candidates if isinstance(candidate, dict)
            ] if isinstance(candidates, list) else [],
            details=payload,
        ))
    return intents
