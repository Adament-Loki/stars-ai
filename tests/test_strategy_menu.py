"""The director menu offers only orders the native writer can already emit."""
from __future__ import annotations

import unittest

from stars_ai.core.models import Fleet, GameState, Planet, Position, RaceProfile, Tech
from stars_ai.strategy_menu import build_option_menu


def _state(**native) -> GameState:
    base = {
        "current_research_field": "propulsion",
        "turn_combat": {"decision": "hold_and_tech", "offensive": False, "authoritative": True},
    }
    base.update(native)
    return GameState(
        game_name="menu",
        year=2412,
        player_id=1,
        race=RaceProfile(),
        tech=Tech(),
        planets=[
            Planet(1, "Home", Position(0, 0), owner=1, habitability=80, native={"is_homeworld": True}),
            Planet(2, "Owned", Position(10, 0), owner=1, habitability=70, native={"has_starbase": True}),
            Planet(3, "Green", Position(30, 0), owner=None, habitability=55, observed=True),
            Planet(4, "Far", Position(400, 0), owner=None, habitability=90, observed=True),
        ],
        fleets=[
            Fleet(9, "Raider", 2, Position(20, 0), role="combat"),
        ],
        native=base,
    )


class StrategyMenuTests(unittest.TestCase):
    def test_menu_marks_the_engine_choice_and_hides_blocked_orders(self):
        menu = build_option_menu(_state())
        ids = [row["option_id"] for row in menu["options"]]
        self.assertIn("research:propulsion", menu["engine_choices"])
        self.assertIn("colonize:3", ids)
        self.assertNotIn("colonize:2", ids)
        self.assertIn("queue:1:mines", menu["engine_choices"])
        self.assertIn("front:2:hold", menu["engine_choices"])
        self.assertNotIn("front:2:attack", menu["engine_choices"])
        kinds = {row["order_kind"] for row in menu["options"]}
        self.assertTrue(kinds <= {"research_change", "colony_operation", "set_planet_queue", "move_fleet"})
        blob = " ".join(ids + list(kinds))
        self.assertNotIn("packet", blob)
        self.assertNotIn("set_battle_plan", blob)
        for row in menu["options"]:
            self.assertIn(row["capability"], {"VALIDATED", "PARTIAL"})

    def test_retreat_posture_selects_retreat_on_each_front(self):
        menu = build_option_menu(_state(turn_combat={
            "decision": "retreat_and_preserve",
            "offensive": False,
            "authoritative": True,
        }))
        self.assertIn("front:2:retreat", menu["engine_choices"])
        self.assertNotIn("front:2:attack", menu["engine_choices"])


if __name__ == "__main__":
    unittest.main()
