"""Contracts for the Phase 4 planner and post-planning policy adapters."""

from __future__ import annotations

import json
from pathlib import Path

from stars_ai.adapters.json_adapter import JsonTurnAdapter
from stars_ai.ai import TurnInput, TurnPlanner
from stars_ai.ai.context import TurnContext
from stars_ai.ai.planners import DEFAULT_PLANNERS, DEFAULT_POST_PLANNING_POLICIES, run_pipeline
from stars_ai.ai.planners import default as planner_module
from stars_ai.memory import AgentMemory
from stars_ai.models import OrderSet
from stars_ai.persona import BalancedPersona


EXPANSION_FIXTURE = (
    Path(__file__).parent / "regression" / "fixtures" / "expansion_turn" / "fixture.json"
)


def _fixture_state():
    fixture = json.loads(EXPANSION_FIXTURE.read_text(encoding="utf-8"))
    state_path = (EXPANSION_FIXTURE.parent / fixture["state_source"]).resolve()
    return JsonTurnAdapter().read_state(state_path, player_id=2)


def _context() -> TurnContext:
    state = _fixture_state()
    memory = AgentMemory()
    persona = BalancedPersona()
    context = TurnContext(
        turn_input=TurnInput(state=state, player_id=state.player_id, memory=memory, persona=persona),
        state=state,
        memory=memory,
        persona=persona,
        orders=OrderSet(state.game_name, state.year, state.player_id),
    )
    # Delegation tests replace all legacy functions, so these only prove that
    # wrappers pass the existing context values through unchanged.
    context.strategic_plan = object()  # type: ignore[assignment]
    context.research_decision = object()  # type: ignore[assignment]
    return context


def test_registered_planner_and_policy_order_is_explicit_and_protocol_compatible():
    assert [planner.name for planner in DEFAULT_PLANNERS] == [
        "scout_split_preflight",
        "diplomacy",
        "military",
        "tactical_fleet_management",
        "economy",
        "exploration",
        "research",
        "design_development",
        "strategic_lookahead",
    ]
    assert [policy.name for policy in DEFAULT_POST_PLANNING_POLICIES] == [
        "recon_deconfliction",
        "route_warp_optimization",
        "fuel_safety",
        "fleet_activity",
        "scout_split_enforcement",
    ]
    assert all(isinstance(stage, TurnPlanner) for stage in (
        *DEFAULT_PLANNERS,
        *DEFAULT_POST_PLANNING_POLICIES,
    ))


def test_every_wrapper_delegates_to_its_existing_domain_function(monkeypatch):
    calls = []
    intents = [{"fleet_id": 7, "action": "HOLD"}]
    delegated_names = (
        "prepare_scout_split_precedence",
        "add_diplomacy_orders",
        "add_military_orders",
        "add_tactical_fleet_management",
        "add_economic_orders",
        "add_exploration_orders",
        "add_research_orders",
        "add_design_development_orders",
        "augment_orders_v4",
        "deconflict_recon_orders",
        "optimize_active_route_warps",
        "apply_fuel_safety",
        "ensure_fleet_activity",
        "enforce_scout_split_precedence",
    )

    for name in delegated_names:
        def record(*args, _name=name, **kwargs):
            calls.append(_name)
            return intents if _name == "ensure_fleet_activity" else None

        monkeypatch.setattr(planner_module, name, record)

    context = _context()
    run_pipeline(context, DEFAULT_PLANNERS)
    run_pipeline(context, DEFAULT_POST_PLANNING_POLICIES)

    assert calls == list(delegated_names)
    assert context.legacy_fleet_intents == intents
