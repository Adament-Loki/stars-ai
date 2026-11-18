"""The strategy digest is a compact reading of a saved native turn."""
from __future__ import annotations

import unittest
from pathlib import Path

from stars_ai.core.models import Fleet, GameState, Planet, Position, RaceProfile, Tech
from stars_ai.strategy_digest import TOKEN_BUDGET, build_strategy_digest, digest_saved_turn, estimate_tokens

SANDBOX = Path("tests/TestResource/sandboxes/sandbox-native-reference")


def _state() -> GameState:
    return GameState(
        game_name="digest",
        year=2410,
        player_id=1,
        race=RaceProfile(),
        tech=Tech(energy=3, weapons=2, propulsion=4, construction=1, electronics=2, biotechnology=1),
        planets=[
            Planet(1, "Home", Position(0, 0), owner=1, population=25000, factories=10, mines=8, defenses=4, ironium=20, boranium=5, germanium=12, native={"is_homeworld": True}),
            Planet(2, "Theirs", Position(40, 0), owner=2, population=8000, defenses=2, ironium=3, boranium=1, germanium=0, observed=True),
        ],
        fleets=[
            Fleet(1, "Scout", 1, Position(0, 0), role="scout", native={"ship_count": [1], "cargo": {"ironium": 0, "boranium": 0, "germanium": 0, "population": 0}, "fuel": 40}),
            Fleet(4, "Raider", 2, Position(30, 0), role="combat", native={"ship_count": [0, 1], "cargo": {}, "fuel": 10}),
        ],
        native={
            "header": {"turn": 10, "year": 2410},
            "current_research_field": "propulsion",
            "next_research_field": "weapons",
            "research_allocation_percent": 15,
            "current_player_score": {"rank": 2, "score": 1200, "planets": 1, "tech_total": 13},
            "score_visibility": "private",
            "designs": [{"design_number": 0, "name": "Probe"}],
        },
    )


class StrategyDigestTests(unittest.TestCase):
    def test_synthetic_turn_lists_planets_fleets_tech_and_a_nearby_threat(self):
        digest = build_strategy_digest(_state())
        self.assertEqual(digest["turn"], 10)
        self.assertEqual(digest["year"], 2410)
        self.assertEqual(digest["our_planets"][0]["name"], "Home")
        self.assertEqual(digest["our_planets"][0]["factories"], 10)
        self.assertEqual(digest["our_planets"][0]["mines"], 8)
        self.assertEqual(digest["our_fleets"][0]["designs"][0]["name"], "Probe")
        self.assertEqual(digest["our_fleets"][0]["fuel"], 40)
        self.assertEqual(digest["enemy_planets"][0]["name"], "Theirs")
        self.assertEqual(digest["tech"]["propulsion"], 4)
        self.assertEqual(digest["research"]["current_field"], "propulsion")
        self.assertEqual(digest["score"]["score"], 1200)
        self.assertEqual(digest["threats"][0]["fleet_id"], 4)
        self.assertLess(estimate_tokens(digest), TOKEN_BUDGET)

    def test_saved_sandbox_turn_stays_inside_the_token_budget(self):
        m_path = SANDBOX / "GAME.m1"
        xy_path = SANDBOX / "GAME.xy"
        h_path = SANDBOX / "GAME.h1"
        if not m_path.is_file() or not xy_path.is_file():
            self.skipTest("sandbox turn files are not in this checkout")
        digest = digest_saved_turn(m_path, xy_path, h_path if h_path.is_file() else None, player_id=1)
        self.assertGreaterEqual(digest["year"], 2400)
        self.assertEqual(digest["player_id"], 1)
        self.assertTrue(digest["our_planets"])
        home = digest["our_planets"][0]
        for key in ("population", "ironium", "boranium", "germanium", "factories", "mines", "defenses"):
            self.assertIn(key, home)
        self.assertIn("fleets", "our_fleets" and "our_fleets")
        self.assertTrue(digest["our_fleets"])
        fleet = digest["our_fleets"][0]
        for key in ("x", "y", "designs", "cargo", "fuel"):
            self.assertIn(key, fleet)
        self.assertIn("score", digest)
        self.assertIn("tech", digest)
        self.assertIn("research", digest)
        self.assertIn("threats", digest)
        self.assertIn("enemy_fleets", digest)
        self.assertIsNotNone(digest["sources"]["h"])
        self.assertLessEqual(digest["token_estimate"], TOKEN_BUDGET)
        self.assertLess(estimate_tokens(digest), TOKEN_BUDGET)


if __name__ == "__main__":
    unittest.main()
