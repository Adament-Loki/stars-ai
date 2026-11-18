"""The director cannot override the hard safety limits."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stars_ai.core.models import Fleet, GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.llm_director import StubDirectorProvider
from stars_ai.strategy_director_session import consult_strategy_director
from stars_ai.strategy_guardrails import rejection


def _state() -> GameState:
    return GameState(
        game_name="guards",
        year=2406,
        player_id=1,
        race=RaceProfile(),
        tech=Tech(),
        planets=[
            Planet(1, "Home", Position(0, 0), owner=1, defenses=0, native={"is_homeworld": True}),
            Planet(2, "Mine", Position(15, 0), owner=1, native={"has_starbase": True}),
            Planet(3, "Open", Position(40, 0), owner=None, habitability=70, observed=True),
        ],
        fleets=[Fleet(1, "Only", 1, Position(0, 0), role="combat")],
        native={
            "header": {"turn": 6},
            "current_research_field": "energy",
            "turn_combat": {"decision": "retreat_and_preserve", "offensive": False, "authoritative": True},
        },
    )


class StrategyGuardrailTests(unittest.TestCase):
    def test_retreat_posture_blocks_an_attack_and_owned_worlds_cannot_be_colonized(self):
        state = _state()
        self.assertIn("RETREAT_AND_PRESERVE", rejection(state, {
            "category": "front", "payload": {"action": "attack", "planet_id": 3},
        }))
        self.assertIn("owned planet", rejection(state, {
            "category": "colonize", "payload": {"destination_planet_id": 2},
        }))
        state.native["turn_combat"] = {"decision": "fight_now", "offensive": True, "authoritative": True}
        self.assertIn("undefended", rejection(state, {
            "category": "front", "payload": {"action": "attack", "planet_id": 3},
        }))

    def test_a_zero_token_budget_skips_the_provider_and_keeps_the_engine_order(self):
        with tempfile.TemporaryDirectory() as folder:
            state = _state()
            state.native.update({
                "llm_director_mode": "decide",
                "llm_director_max_tokens": 1,
                "llm_director_log_dir": folder,
                "llm_director_provider": StubDirectorProvider(json.dumps({
                    "strategic_posture": "attack",
                    "choices": [{"option_id": "research:weapons", "reason": "should not run"}],
                    "notes_for_next_turn": "",
                })),
            })
            orders = OrderSet("guards", 2406, 1)
            orders.add("set_research", {"current_field": "energy", "next_field": "energy"}, "engine")
            record = consult_strategy_director(state, orders)
            self.assertEqual(orders.orders[0].payload["current_field"], "energy")
            self.assertTrue(any("token budget" in row["reason"] for row in record["decision"]["fallbacks"]))
            self.assertEqual(state.native["llm_director_provider"].response, json.dumps({
                "strategic_posture": "attack",
                "choices": [{"option_id": "research:weapons", "reason": "should not run"}],
                "notes_for_next_turn": "",
            }))


if __name__ == "__main__":
    unittest.main()
