"""Compatibility tests for the Phase 1 public AI facade."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from stars_ai.agent import StarsAgent
from stars_ai.adapters.json_adapter import JsonTurnAdapter
from stars_ai.ai import StarsAI, TurnInput, TurnPlanner
from stars_ai.memory import AgentMemory


EXPANSION_FIXTURE = Path(__file__).parent / "regression" / "fixtures" / "expansion_turn" / "fixture.json"


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
    assert decision.context.legacy_agent is not None
    assert decision.ai_state is decision.context.ai_state
    assert decision.ai_state.game is facade_state
    assert asdict(decision.ai_state.persistent.to_agent_memory()) == asdict(facade_memory)
    assert decision.ai_state.turn.strategic_phase is not None
    assert decision.ai_state.turn.strategic_watchdog is not None
    assert decision.ai_state.turn.research_decision is not None


def test_turn_planner_is_a_structural_protocol_only():
    class PlaceholderPlanner:
        name = "placeholder"

        def plan(self, context) -> None:
            context.decision_events.append({"kind": "placeholder"})

    assert isinstance(PlaceholderPlanner(), TurnPlanner)
