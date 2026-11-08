"""Compatibility tests for the Phase 1 public AI facade."""

from __future__ import annotations

from dataclasses import asdict

import stars_ai.ai.engine as engine_module
from stars_ai.agent import StarsAgent
from stars_ai.adapters.json_adapter import JsonTurnAdapter
from stars_ai.ai import StarsAI, TurnInput, TurnPlanner
from stars_ai.ai.planners import DEFAULT_PLANNERS, DEFAULT_POST_PLANNING_POLICIES
from stars_ai.memory import AgentMemory
from tests.helpers.resources import test_resource as resource_path


EXPANSION_FIXTURE = resource_path("regression", "expansion_turn", "fixture.json")


def _fixture_state():
    import json

    fixture = json.loads(EXPANSION_FIXTURE.read_text(encoding="utf-8"))
    state_path = (EXPANSION_FIXTURE.parent / fixture["state_source"]).resolve()
    return JsonTurnAdapter().read_state(state_path, player_id=2)


def test_stars_ai_facade_matches_legacy_semantic_orders_and_memory():
    legacy_state = _fixture_state()
    facade_state = _fixture_state()
    legacy_memory = AgentMemory()
    facade_memory = AgentMemory()

    legacy_orders = StarsAgent(legacy_state, memory=legacy_memory).play_turn()
    decision = StarsAI().run_turn(TurnInput(
        state=facade_state,
        player_id=facade_state.player_id,
        memory=facade_memory,
    ))

    assert decision.orders.to_dict() == legacy_orders.to_dict()
    assert asdict(decision.context.memory) == asdict(legacy_memory)
    assert decision.context.state.to_dict() == legacy_state.to_dict()
    assert decision.context.state is facade_state
    assert decision.context.orders is decision.orders
    assert decision.context.legacy_agent is None
    assert decision.ai_state is decision.context.ai_state
    assert decision.ai_state.game is facade_state
    assert asdict(decision.ai_state.persistent.to_agent_memory()) == asdict(facade_memory)
    assert decision.ai_state.turn.strategic_phase is not None
    assert decision.ai_state.turn.strategic_watchdog is not None
    assert decision.ai_state.turn.research_decision is not None


def test_facade_records_direct_planner_decision_evidence():
    state = _fixture_state()
    decision = StarsAI().run_turn(TurnInput(
        state=state,
        player_id=state.player_id,
        memory=AgentMemory(),
    ))

    # ``decision_events`` remains the legacy list, while the new journal
    # records strategy/research choices and orders at the planner boundary.
    assert isinstance(decision.decision_events, list)
    events = decision.decision_journal.to_dict()["events"]
    domains = {event["domain"] for event in events}
    assert {"strategy", "research"} <= domains
    assert any(event["domain"] == "exploration" for event in events)
    assert any(event["reason"] for event in events)


def test_turn_planner_is_a_structural_protocol_only():
    class PlaceholderPlanner:
        name = "placeholder"

        def plan(self, context) -> None:
            context.decision_events.append({"kind": "placeholder"})

    assert isinstance(PlaceholderPlanner(), TurnPlanner)


def test_legacy_agent_delegates_to_stars_ai_and_retains_legacy_attributes(monkeypatch):
    calls = []
    original = StarsAI.run_turn

    def tracking_run_turn(self, turn_input):
        calls.append(turn_input)
        return original(self, turn_input)

    monkeypatch.setattr(StarsAI, "run_turn", tracking_run_turn)
    state = _fixture_state()
    memory = AgentMemory()
    agent = StarsAgent(state, memory=memory)

    orders = agent.play_turn()

    assert len(calls) == 1
    assert calls[0].state is state
    assert calls[0].memory is memory
    assert orders.game_name == state.game_name
    assert agent.last_plan is not None
    assert agent.fleet_intents


def test_stars_ai_preserves_the_established_planner_and_safety_order(monkeypatch):
    calls = []
    original = engine_module.run_pipeline

    def record(context, stages):
        calls.append(tuple(stage.name for stage in stages))
        return original(context, stages)

    monkeypatch.setattr(engine_module, "run_pipeline", record)

    state = _fixture_state()
    StarsAI().run_turn(TurnInput(state=state, player_id=state.player_id, memory=AgentMemory()))

    assert calls == [
        tuple(planner.name for planner in DEFAULT_PLANNERS),
        tuple(policy.name for policy in DEFAULT_POST_PLANNING_POLICIES),
    ]
