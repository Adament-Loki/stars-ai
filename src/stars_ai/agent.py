from __future__ import annotations
from .models import GameState, OrderSet
from .memory import AgentMemory
from .persona import StrategicPersona, BalancedPersona, StrategicPlan
from .strategy.economy import add_economic_orders
from .strategy.exploration import add_exploration_orders, deconflict_recon_orders
from .strategy.research import add_research_orders
from .research_planner import plan_research
from .strategy.military import add_military_orders
from .strategy.diplomacy import add_diplomacy_orders
from .v4_coordinator import augment_orders_v4
from .fleet_intent import ensure_fleet_activity
from .fuel_planner import apply_fuel_safety
from .design_development import add_design_development_orders
from .strategic_watchdog import evaluate_strategic_watchdog


class StarsAgent:
    def __init__(
        self,
        state: GameState,
        memory: AgentMemory | None = None,
        persona: StrategicPersona | None = None,
    ):
        self.state = state
        self.memory = memory or AgentMemory()
        self.persona = persona or BalancedPersona()
        self.last_plan: StrategicPlan | None = None
        self.fleet_intents: list[dict] = []

    def play_turn(self) -> OrderSet:
        orders = OrderSet(
            game_name=self.state.game_name,
            year=self.state.year,
            player_id=self.state.player_id,
        )

        # v7.0: merge this year's real M observations into persistent memory,
        # then restore older learned planet intelligence before any strategy runs.
        intel_diag = self.memory.reconcile_state(self.state)
        self.state.native["persistent_intel"] = dict(intel_diag)
        self.memory.sync_scout_routes_from_native(self.state)
        command_outcomes=self.memory.evaluate_action_outcomes(self.state)
        self.state.native["command_outcomes"]=command_outcomes
        movement_diagnostics=self.memory.update_movement_progress(self.state)
        self.state.native["movement_progress_diagnostics"]=movement_diagnostics
        self.state.native["recent_scan_targets"] = sorted(
            self.memory.recent_scan_target_ids(self.state.year, cooldown_years=3)
        )

        watchdog = evaluate_strategic_watchdog(self.state, self.memory)
        self.state.native["strategic_watchdog"] = dict(watchdog)

        plan = self.persona.build_plan(self.state)
        self.last_plan = plan
        orders.notes.append(f"Persona: {plan.persona_name}")
        orders.notes.append(
            f"PERSISTENT INTEL: current M observed={intel_diag['current_m_observed']}, "
            f"ever observed={intel_diag['ever_observed']}/{intel_diag['total_planets']}, "
            f"restored from memory={intel_diag['restored_from_memory']}."
        )
        orders.notes.extend(
            outcome["message"] for outcome in command_outcomes
            if outcome.get("status") in ("WARNING","UNVERIFIED")
        )
        orders.notes.extend(watchdog.get("notes", []))
        orders.notes.extend(plan.notes)

        # Research is a strategic plan consumed by production, not a late field
        # balancing afterthought. Contributor/protection choices therefore exist
        # before any planet queue is generated.
        research_decision = plan_research(self.state, plan, self.memory)
        self.state.native["research_strategy"] = research_decision.to_payload()
        orders.notes.append(f"RESEARCH PLAN: {research_decision.reason}")
        for unlocked in research_decision.recently_unlocked:
            orders.notes.append(
                f"RESEARCH UNLOCK: {unlocked} completed; execute its post-unlock action and select the next capability."
            )

        add_diplomacy_orders(self.state, orders, plan)
        add_military_orders(self.state, orders, plan)
        add_economic_orders(self.state, orders, plan, research_decision=research_decision)
        add_exploration_orders(self.state, orders, plan, memory=self.memory)
        add_research_orders(self.state, orders, plan, decision=research_decision)
        add_design_development_orders(self.state, orders, plan)
        augment_orders_v4(self.state, orders, plan)
        deconflict_recon_orders(self.state,orders)
        apply_fuel_safety(self.state, orders)

        # Fleet-activity invariant: every owned fleet must have an explicit
        # objective, continuation, conscious hold, or surfaced BLOCKED reason.
        fleet_intents = ensure_fleet_activity(self.state, orders, plan)
        self.fleet_intents = fleet_intents

        # Native-state diagnostics for playtest triage.
        for f in self.state.fleets:
            if f.owner == self.state.player_id:
                orders.notes.append(
                    f"FLEET STATE: id={f.id} role={f.role} "
                    f"pos=({int(f.position.x)},{int(f.position.y)}) "
                    f"dest={f.destination_planet_id} destination_warp={f.destination_warp} "
                    f"destination_task={f.destination_task} speed={f.speed}"
                )
        for diagnostic in movement_diagnostics:
            if diagnostic.get("actual_progress") is None:
                continue
            orders.notes.append(
                f"MOVEMENT PROGRESS: fleet={diagnostic['fleet_id']} "
                f"destination={diagnostic['destination_planet_id']} "
                f"range {diagnostic['prior_range']}->{diagnostic['current_range']} "
                f"at W{diagnostic['commanded_warp']}; expected~{diagnostic['expected_movement']} "
                f"actual={diagnostic['actual_progress']}"
            )
            if diagnostic.get("flag"):
                orders.notes.append(diagnostic["flag"])

        owned_count=sum(1 for p in self.state.planets if p.owner==self.state.player_id)
        scout_count=sum(1 for f in self.state.fleets if f.owner==self.state.player_id and f.role=="scout")
        move_count=sum(1 for o in orders.orders if o.kind=="move_fleet")
        if self.state.year <= 2425 and owned_count <= 2 and scout_count > 0 and move_count == 0:
            orders.notes.append("EXPANSION WATCHDOG: early empire has scouts but generated no movement orders.")

        orders.orders.sort(key=lambda o: o.priority, reverse=True)

        # Non-native adapters commit semantic intent directly. The native writer
        # snapshots this state, rolls it back, and reapplies emitted routes only.
        self.memory.record_scan_orders(orders, self.state.year)
        watchdog = evaluate_strategic_watchdog(self.state, self.memory)
        self.state.native["strategic_watchdog"] = dict(watchdog)
        self.memory.append_kpi(watchdog)

        self.memory.last_year = self.state.year
        self.memory.strategic_notes = orders.notes[-75:]
        self.memory.goal_progress = {
            **dict(plan.goal_progress),
            "explored_count": float(watchdog["explored_count"]),
            "explored_percent": float(watchdog["explored_percent"]),
            "new_colonies": float(watchdog["new_colonies"]),
            "exploration_pressure": float(watchdog["exploration_pressure"]),
            "colonization_pressure": float(watchdog["colonization_pressure"]),
        }
        self.memory.diplomacy = {str(k): dict(v) for k, v in plan.diplomacy.items()}
        return orders

    def _observe(self) -> None:
        """Compatibility shim retained for external callers."""
        self.memory.reconcile_state(self.state)
