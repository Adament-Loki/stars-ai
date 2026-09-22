"""Application-layer coordination between AI decisions and native serialization.

This module is intentionally the only place that knows both how to decode a
native turn and how to ask :class:`StarsAI` for semantic orders.  The native
writer remains a pure serializer and safety boundary.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .adapters.native_core_adapter import NativeCoreTurnAdapter
from .ai import StarsAI, TurnDecision, TurnInput
from .ai.state_store import AIStateStore
from .memory import AgentMemory
from .native.x_writer import NativeWriteResult, write_native_orders
from .persona import (
    BalancedPersona,
    ExpansionistPersona,
    IndustrialistPersona,
    MilitaristPersona,
    TechnologistPersona,
)
from .reporting import (
    TurnReportModel,
    build_native_decision_report,
    build_turn_report_model,
)


def _persona(name: str):
    normalized = (name or "Balanced").lower()
    if normalized == "expansionist":
        return ExpansionistPersona()
    if normalized == "industrialist":
        return IndustrialistPersona()
    if normalized == "technologist":
        return TechnologistPersona()
    if normalized == "militarist":
        return MilitaristPersona()
    return BalancedPersona()


def _configured_friend_ids(player_id: int, friend_player_ids: list[int] | None) -> list[int]:
    return sorted({
        int(candidate)
        for candidate in (friend_player_ids or [])
        if int(candidate) != int(player_id)
    })


@dataclass(slots=True)
class PlannedNativeAITurn:
    """A completed semantic decision awaiting native serialization."""

    state: object
    decision: TurnDecision
    actual_relations: list[int]
    prior_scout_routes: dict
    prior_scan_history: dict


@dataclass(slots=True)
class NativeAITurnExecution:
    """Semantic decision, native audit, and optional human report for one turn."""

    planned: PlannedNativeAITurn
    native_result: NativeWriteResult
    report_model: TurnReportModel


def plan_native_ai_turn(
    *,
    player_id: int,
    m_path: str | Path,
    xy_path: str | Path,
    persona_name: str = "Balanced",
    friend_player_ids: list[int] | None = None,
    state_store: AIStateStore | None = None,
) -> PlannedNativeAITurn:
    """Decode a native turn and produce semantic orders without writing an X file."""

    state = NativeCoreTurnAdapter(xy_path=Path(xy_path)).read_state(Path(m_path), player_id)
    requested_friends = _configured_friend_ids(player_id, friend_player_ids)
    actual_relations = list(state.race.native.get("player_relations", []))
    state.native["actual_player_relations"] = list(actual_relations)

    # This remains before strategy because a configured ally must not be
    # selected as a military target while the native Friend order is pending.
    effective_relations = list(actual_relations)
    if requested_friends:
        max_target = max(requested_friends)
        if len(effective_relations) < max_target:
            effective_relations.extend([0] * (max_target - len(effective_relations)))
        for target in requested_friends:
            effective_relations[target - 1] = 1
        state.race.native["player_relations"] = effective_relations

    memory = (
        state_store.load().to_agent_memory()
        if state_store is not None else AgentMemory()
    )
    prior_scout_routes = deepcopy(memory.scout_routes)
    prior_scan_history = deepcopy(memory.scan_target_history)
    decision = StarsAI().run_turn(TurnInput(
        state=state,
        player_id=int(player_id),
        memory=memory,
        persona=_persona(persona_name),
    ))

    # Record what strategy requested before native gating resolves its result.
    memory.record_capability_plans(decision.orders, state)
    state.native["capability_execution"] = memory.capability_execution_payload()

    # Emit a semantic Friend order only until the observed M file confirms it.
    for target in requested_friends:
        actual = actual_relations[target - 1] if 0 <= target - 1 < len(actual_relations) else 0
        if int(actual) != 1:
            decision.orders.add(
                "set_player_relation",
                {"player_id": target, "relation": "friend", "configured_alliance": True},
                f"Configured reciprocal alliance: mark Player {target} as Friend.",
                priority=150,
            )
    decision.orders.orders.sort(key=lambda order: order.priority, reverse=True)
    return PlannedNativeAITurn(
        state=state,
        decision=decision,
        actual_relations=actual_relations,
        prior_scout_routes=prior_scout_routes,
        prior_scan_history=prior_scan_history,
    )


def finalize_native_ai_turn(
    planned: PlannedNativeAITurn,
    native_result: NativeWriteResult,
    *,
    state_store: AIStateStore | None = None,
) -> None:
    """Stage only native-surviving memory effects after a successful write."""

    memory = planned.decision.context.memory
    memory.scout_routes = planned.prior_scout_routes
    memory.scan_target_history = planned.prior_scan_history
    memory.prune_scout_routes(planned.state)
    memory.sync_scout_routes_from_native(planned.state)
    memory.record_emitted_scan_orders(native_result.emitted, planned.state.year)
    memory.record_emitted_actions(native_result.emitted, planned.state)
    memory.record_native_capability_execution(
        native_result.emitted,
        native_result.skipped,
        planned.state,
    )
    planned.state.native["capability_execution"] = memory.capability_execution_payload()
    if state_store is not None:
        state_store.stage(memory)


def _write_trace(
    path: Path,
    planned: PlannedNativeAITurn,
    native_result: NativeWriteResult,
    *,
    memory_path: Path | None,
    memory_output_path: Path | None,
) -> None:
    """Preserve the existing forensic trace outside the serializer."""

    state = planned.state
    memory = planned.decision.context.memory
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "state": {"game": state.game_name, "year": state.year, "player": state.player_id},
        "orders": planned.decision.orders.to_dict(),
        "fleet_intents": planned.decision.context.legacy_fleet_intents,
        "x_lifecycle": native_result.native_audit,
        "persistent_intel": state.native.get("persistent_intel", {}),
        "strategic_watchdog": state.native.get("strategic_watchdog", {}),
        "probe_route_diagnostics": state.native.get("probe_route_diagnostics", []),
        "movement_progress_diagnostics": state.native.get("movement_progress_diagnostics", []),
        "command_outcomes": state.native.get("command_outcomes", []),
        "pending_command_expectations": dict(memory.action_expectations),
        "capability_execution": memory.capability_execution_payload(),
        "waypoint_diagnostics": native_result.waypoint_diagnostics,
        "persistent_scout_routes": dict(memory.scout_routes),
        "memory_path": str(memory_path) if memory_path else None,
        "memory_output_path": str(memory_output_path) if memory_output_path else None,
        "native_result": asdict(native_result),
    }, indent=2), encoding="utf-8")
    decision_report_path = path.with_name(
        path.name.replace("-decision-native.json", "-DECISION_REPORT.txt")
    )
    decision_report_path.write_text(build_native_decision_report(
        state,
        planned.decision.orders,
        planned.decision.context.legacy_fleet_intents,
        native_result.emitted,
        native_result.skipped,
        native_result.waypoint_diagnostics,
    ), encoding="utf-8")


def execute_native_ai_turn(
    *,
    player_id: int,
    m_path: str | Path,
    xy_path: str | Path,
    template_x_path: str | Path,
    output_x_path: str | Path,
    client_x_path: str | Path | None = None,
    persona_name: str = "Balanced",
    friend_player_ids: list[int] | None = None,
    state_store: AIStateStore | None = None,
    trace_path: str | Path | None = None,
) -> NativeAITurnExecution:
    """Run the application lifecycle around the pure native serializer."""

    planned = plan_native_ai_turn(
        player_id=player_id,
        m_path=m_path,
        xy_path=xy_path,
        persona_name=persona_name,
        friend_player_ids=friend_player_ids,
        state_store=state_store,
    )
    native_result = write_native_orders(
        game_state=planned.state,
        orders=planned.decision.orders,
        template_x_path=Path(template_x_path),
        output_x_path=Path(output_x_path),
        client_x_path=Path(client_x_path) if client_x_path else None,
    )
    finalize_native_ai_turn(planned, native_result, state_store=state_store)
    if trace_path is not None:
        _write_trace(
            Path(trace_path),
            planned,
            native_result,
            memory_path=state_store.committed_path if state_store else None,
            memory_output_path=state_store.pending_path if state_store else None,
        )
    report_model = build_turn_report_model(
        planned.state,
        planned.decision.orders,
        planned.decision,
        native_result.emitted,
        native_result.skipped,
        native_result.waypoint_diagnostics,
    )
    return NativeAITurnExecution(planned, native_result, report_model)


def write_ai_turn(
    *,
    player_id: int,
    m_path: str | Path,
    xy_path: str | Path,
    template_x_path: str | Path,
    output_x_path: str | Path,
    client_x_path: str | Path | None = None,
    persona_name: str = "Balanced",
    trace_path: str | Path | None = None,
    friend_player_ids: list[int] | None = None,
    memory_path: str | Path | None = None,
    memory_output_path: str | Path | None = None,
) -> NativeWriteResult:
    """Compatibility facade for callers that formerly invoked the X writer.

    The facade is intentionally outside ``native.x_writer``.  New application
    code should use :func:`execute_native_ai_turn` or call
    :func:`write_native_orders` with an existing semantic decision.
    """

    committed = Path(memory_path) if memory_path else None
    pending = Path(memory_output_path) if memory_output_path else None
    state_store = AIStateStore(committed, pending) if committed else None
    execution = execute_native_ai_turn(
        player_id=player_id,
        m_path=m_path,
        xy_path=xy_path,
        template_x_path=template_x_path,
        output_x_path=output_x_path,
        client_x_path=client_x_path,
        persona_name=persona_name,
        friend_player_ids=friend_player_ids,
        state_store=state_store,
        trace_path=trace_path,
    )
    if state_store is not None and pending is None:
        # The historic direct API wrote through synchronously.  Autohost uses
        # ``execute_native_ai_turn`` and retains the staged transaction instead.
        state_store.commit()
    elif state_store is None and pending is not None:
        execution.planned.decision.context.memory.save(pending)
    return execution.native_result
