"""Advise records a choice. Decide retargets an order the engine already made."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stars_ai.core.models import GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.llm_director import StubDirectorProvider
from stars_ai.strategy_director_session import consult_strategy_director


def _state(mode: str, folder: Path) -> GameState:
    return GameState(
        game_name="session",
        year=2404,
        player_id=1,
        race=RaceProfile(),
        tech=Tech(),
        planets=[Planet(1, "Home", Position(0, 0), owner=1, native={"is_homeworld": True})],
        fleets=[],
        native={
            "header": {"turn": 4},
            "current_research_field": "energy",
            "llm_director_mode": mode,
            "llm_director_log_dir": str(folder),
            "llm_director_notes_path": str(folder / "notes.json"),
            "llm_director_provider": StubDirectorProvider(json.dumps({
                "strategic_posture": "research",
                "choices": [{"option_id": "research:weapons", "reason": "weapons first"}],
                "notes_for_next_turn": "stay on weapons",
            })),
        },
    )


class StrategyDirectorSessionTests(unittest.TestCase):
    def test_off_does_not_touch_orders_or_write_a_log(self):
        with tempfile.TemporaryDirectory() as folder:
            state = _state("off", Path(folder))
            orders = OrderSet("session", 2404, 1)
            orders.add("set_research", {"current_field": "energy", "next_field": "energy"}, "engine")
            self.assertIsNone(consult_strategy_director(state, orders))
            self.assertEqual(orders.orders[0].payload["current_field"], "energy")
            self.assertEqual(list(Path(folder).glob("*.json")), [])

    def test_advise_logs_the_choice_and_leaves_the_order(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = _state("advise", root)
            orders = OrderSet("session", 2404, 1)
            orders.add("set_research", {"current_field": "energy", "next_field": "energy"}, "engine")
            record = consult_strategy_director(state, orders)
            self.assertEqual(orders.orders[0].payload["current_field"], "energy")
            self.assertEqual(record["decision"]["choices"][0]["option_id"], "research:weapons")
            log = json.loads((root / "turn-4-player-1.json").read_text(encoding="utf-8"))
            self.assertIn("research:weapons", json.dumps(log))
            self.assertEqual(json.loads((root / "notes.json").read_text(encoding="utf-8"))["notes_for_next_turn"], "stay on weapons")

    def test_decide_retargets_the_research_order_the_engine_emitted(self):
        with tempfile.TemporaryDirectory() as folder:
            state = _state("decide", Path(folder))
            orders = OrderSet("session", 2404, 1)
            orders.add("set_research", {"current_field": "energy", "next_field": "energy", "allocation_percent": 15}, "engine")
            record = consult_strategy_director(state, orders)
            self.assertEqual(orders.orders[0].payload["current_field"], "weapons")
            self.assertIn("research:weapons", record["applied"])


if __name__ == "__main__":
    unittest.main()
