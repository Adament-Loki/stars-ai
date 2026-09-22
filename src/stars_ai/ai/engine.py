"""Authoritative, behavior-preserving orchestration of one AI turn.

The helpers below deliberately follow the long-standing ``StarsAgent`` order.
They coordinate existing planners only; strategy, persistence, and native
encoding rules remain in their established modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..memory import AgentMemory
from ..models import OrderSet
from ..persona import BalancedPersona, StrategicPlan
from ..research_planner import ResearchDecision, plan_research
from ..strategic_watchdog import evaluate_strategic_watchdog
from .ai_state import AIState, PersistentAIState, TurnAIState, TurnInput
from .context import TurnContext
from .decision_journal import DecisionJournal
from .planners import (
    DEFAULT_PLANNERS,
    DEFAULT_POST_PLANNING_POLICIES,
    run_pipeline,
)
from .result import TurnDecision


@dataclass(slots=True)
class _TurnWork:
    """Explicit, short-lived values shared by the ordered orchestration steps."""

    context: TurnContext
    orders: OrderSet
    intel_diagnostics: dict[str, Any] = field(default_factory=dict)
    command_outcomes: list[dict[str, Any]] = field(default_factory=list)
    movement_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    plan: StrategicPlan | None = None
    research_decision: ResearchDecision | None = None
    legacy_fleet_intents: list[dict[str, Any]] = field(default_factory=list)

    @property
    def state(self):
        return self.context.state

    @property
    def memory(self) -> AgentMemory:
        return self.context.memory

    @property
    def persona(self):
        return self.context.persona

    @property
    def ai_state(self) -> AIState:
        if self.context.ai_state is None:
            raise RuntimeError("Turn orchestration requires AIState")
        return self.context.ai_state


class StarsAI:
    """The authoritative lifecycle for one complete AI player turn.

    The public path is intentionally linear and readable:

    1. initialize observed, persistent, and current-turn state;
    2. reconcile observations and previous actions;
    3. assess strategic position and select research;
    4. run strategic planners in their established order;
    5. resolve movement, safety, and fleet-intent invariants; and
    6. finalize semantic orders and persistent state.

    ``StarsAgent`` delegates here as a compatibility wrapper.
    """

    def run_turn(self, turn_input: TurnInput) -> TurnDecision:
        """Run the complete existing AI lifecycle for one normalized turn."""

        work = self._begin_turn(turn_input)
        self._reconcile_observed_and_persistent_state(work)
        self._evaluate_strategy(work)
        self._choose_research(work)
        self._run_strategic_planners(work)
        self._apply_post_planning_policies(work)
        self._append_turn_diagnostics(work)
        self._finalize_turn(work)
        return TurnDecision(
            orders=work.orders,
            context=work.context,
            decision_events=work.context.decision_events,
            decision_journal=work.context.decision_journal,
        )

    def _begin_turn(self, turn_input: TurnInput) -> _TurnWork:
        """Initialize only the existing working objects and semantic order set."""

        memory = turn_input.memory or AgentMemory()
        persona = turn_input.persona or BalancedPersona()
        ai_state = AIState.from_agent_memory(turn_input.state, memory)
        context = TurnContext(
            turn_input=turn_input,
            state=turn_input.state,
            memory=memory,
            persona=persona,
            ai_state=ai_state,
            decision_journal=DecisionJournal(
                player_id=turn_input.player_id,
                turn=int(turn_input.state.year),
                persona=persona.name,
            ),
        )
        orders = OrderSet(
            game_name=context.state.game_name,
            year=context.state.year,
            player_id=context.state.player_id,
        )
        context.orders = orders
        return _TurnWork(context=context, orders=orders)

    def _reconcile_observed_and_persistent_state(self, work: _TurnWork) -> None:
        """Run legacy observation, action, route, and movement reconciliation."""

        work.intel_diagnostics = work.memory.reconcile_state(work.state)
        work.state.native["persistent_intel"] = dict(work.intel_diagnostics)
        capability_diagnostics = work.memory.reconcile_capability_execution(work.state)
        work.state.native["capability_execution_diagnostics"] = capability_diagnostics
        work.memory.prepare_territorial_exposure(
            work.state,
            work.intel_diagnostics.get("lost_owned_planet_ids", []),
        )
        work.memory.sync_scout_routes_from_native(work.state)
        work.command_outcomes = work.memory.evaluate_action_outcomes(work.state)
        work.state.native["command_outcomes"] = work.command_outcomes
        work.movement_diagnostics = work.memory.update_movement_progress(work.state)
        work.state.native["movement_progress_diagnostics"] = work.movement_diagnostics
        work.state.native["recent_scan_targets"] = sorted(
            work.memory.recent_scan_target_ids(work.state.year, cooldown_years=3)
        )

    def _evaluate_strategy(self, work: _TurnWork) -> None:
        """Evaluate the watchdog, then select the same persona plan as before."""

        watchdog = evaluate_strategic_watchdog(work.state, work.memory)
        work.state.native["strategic_watchdog"] = dict(watchdog)

        work.plan = work.persona.build_plan(work.state)
        work.context.strategic_plan = work.plan
        work.context.decision_journal.record(
            domain="strategy",
            action=f"Strategic phase: {work.plan.phase}",
            reason=work.plan.phase_reason,
            target=work.plan.phase,
            evidence={
                "assessment": dict(work.plan.phase_assessment),
                "objective_weights": dict(work.plan.objective_weights),
                "plan_notes": list(work.plan.notes),
            },
        )
        work.orders.notes.append(f"Persona: {work.plan.persona_name}")
        work.state.native["strategic_phase"] = {
            "phase": work.plan.phase,
            "reason": work.plan.phase_reason,
            "assessment": dict(work.plan.phase_assessment),
        }
        work.orders.notes.append(f"PHASE: {work.plan.phase} â€” {work.plan.phase_reason}")
        work.orders.notes.append(
            f"PERSISTENT INTEL: current M observed={work.intel_diagnostics['current_m_observed']}, "
            f"ever observed={work.intel_diagnostics['ever_observed']}/{work.intel_diagnostics['total_planets']}, "
            f"restored from memory={work.intel_diagnostics['restored_from_memory']}."
        )
        work.orders.notes.extend(
            outcome["message"] for outcome in work.command_outcomes
            if outcome.get("status") in ("WARNING", "UNVERIFIED")
        )
        work.orders.notes.extend(watchdog.get("notes", []))
        work.orders.notes.extend(work.plan.notes)

    def _choose_research(self, work: _TurnWork) -> None:
        """Choose research before production, preserving the research contract."""

        if work.plan is None:
            raise RuntimeError("Research selection requires a strategic plan")
        work.research_decision = plan_research(work.state, work.plan, work.memory)
        work.context.research_decision = work.research_decision
        work.context.decision_journal.record(
            domain="research",
            action="Select research strategy",
            reason=work.research_decision.reason,
            target=work.research_decision.capability_name,
            priority=work.research_decision.allocation_percent,
            evidence=work.research_decision.to_payload(),
        )
        work.state.native["research_strategy"] = work.research_decision.to_payload()
        work.orders.notes.append(f"RESEARCH PLAN: {work.research_decision.reason}")
        for unlocked in work.research_decision.recently_unlocked:
            work.orders.notes.append(
                f"RESEARCH UNLOCK: {unlocked} completed; execute its post-unlock action and select the next capability."
            )

    def _run_strategic_planners(self, work: _TurnWork) -> None:
        """Invoke every legacy strategic planner in its original call order."""

        if work.plan is None or work.research_decision is None:
            raise RuntimeError("Strategic planners require plan and research decision")

        run_pipeline(work.context, DEFAULT_PLANNERS)

    def _apply_post_planning_policies(self, work: _TurnWork) -> None:
        """Apply ordered order-arbitration and fleet-safety policies."""

        run_pipeline(work.context, DEFAULT_POST_PLANNING_POLICIES)
        work.legacy_fleet_intents = work.context.legacy_fleet_intents

    def _append_turn_diagnostics(self, work: _TurnWork) -> None:
        """Append the existing diagnostics before final order sorting and persistence."""

        for fleet in work.state.fleets:
            if fleet.owner == work.state.player_id:
                work.orders.notes.append(
                    f"FLEET STATE: id={fleet.id} role={fleet.role} "
                    f"pos=({int(fleet.position.x)},{int(fleet.position.y)}) "
                    f"dest={fleet.destination_planet_id} destination_warp={fleet.destination_warp} "
                    f"destination_task={fleet.destination_task} speed={fleet.speed}"
                )
        for diagnostic in work.movement_diagnostics:
            if diagnostic.get("actual_progress") is None:
                continue
            work.orders.notes.append(
                f"MOVEMENT PROGRESS: fleet={diagnostic['fleet_id']} "
                f"destination={diagnostic['destination_planet_id']} "
                f"range {diagnostic['prior_range']}->{diagnostic['current_range']} "
                f"at W{diagnostic['commanded_warp']}; expected~{diagnostic['expected_movement']} "
                f"actual={diagnostic['actual_progress']}"
            )
            if diagnostic.get("flag"):
                work.orders.notes.append(diagnostic["flag"])

        owned_count = sum(1 for planet in work.state.planets if planet.owner == work.state.player_id)
        scout_count = sum(
            1 for fleet in work.state.fleets
            if fleet.owner == work.state.player_id and fleet.role == "scout"
        )
        move_count = sum(1 for order in work.orders.orders if order.kind == "move_fleet")
        if work.state.year <= 2425 and owned_count <= 2 and scout_count > 0 and move_count == 0:
            work.orders.notes.append(
                "EXPANSION WATCHDOG: early empire has scouts but generated no movement orders."
            )

    def _finalize_turn(self, work: _TurnWork) -> None:
        """Sort semantic orders, commit legacy memory, and capture typed turn state."""

        if work.plan is None:
            raise RuntimeError("Turn finalization requires a strategic plan")

        work.orders.orders.sort(key=lambda order: order.priority, reverse=True)

        # Non-native adapters commit semantic intent directly. The native writer
        # snapshots this state, rolls it back, and reapplies emitted routes only.
        work.memory.record_scan_orders(work.orders, work.state.year)
        work.memory.commit_territorial_exposure(work.state)
        watchdog = evaluate_strategic_watchdog(work.state, work.memory)
        work.state.native["strategic_watchdog"] = dict(watchdog)
        work.memory.append_kpi(watchdog)

        work.memory.last_year = work.state.year
        work.memory.strategic_notes = work.orders.notes[-75:]
        work.memory.goal_progress = {
            **dict(work.plan.goal_progress),
            "explored_count": float(watchdog["explored_count"]),
            "explored_percent": float(watchdog["explored_percent"]),
            "new_colonies": float(watchdog["new_colonies"]),
            "exploration_pressure": float(watchdog["exploration_pressure"]),
            "colonization_pressure": float(watchdog["colonization_pressure"]),
        }
        work.memory.diplomacy = {
            str(player_id): dict(relation)
            for player_id, relation in work.plan.diplomacy.items()
        }

        for outcome in work.command_outcomes:
            work.context.decision_journal.record(
                domain="previous_turn_outcome",
                action=str(outcome.get("status", "UNVERIFIED")),
                reason=str(outcome.get("message", "No outcome message was recorded.")),
                subject=outcome.get("description"),
                evidence=dict(outcome),
                selected=False,
            )

        work.ai_state.persistent = PersistentAIState.from_agent_memory(work.memory)
        work.ai_state.turn = TurnAIState.from_legacy_native(
            work.state.native,
            fleet_intents=work.legacy_fleet_intents,
        )
