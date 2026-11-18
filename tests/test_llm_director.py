"""The director accepts a menu choice and otherwise keeps the rule engine."""
from __future__ import annotations

import json
import unittest

from stars_ai.llm_director import DirectorError, LLMDirector, StubDirectorProvider


def _menu() -> dict:
    return {
        "options": [
            {"option_id": "research:energy", "category": "research"},
            {"option_id": "research:weapons", "category": "research"},
            {"option_id": "front:1:hold", "category": "front"},
            {"option_id": "front:1:attack", "category": "front"},
        ],
        "engine_choices": ["research:energy", "front:1:hold"],
    }


def _ok(choice: str) -> str:
    return json.dumps({
        "strategic_posture": "hold",
        "choices": [{"option_id": choice, "reason": "test"}],
        "notes_for_next_turn": "watch the border",
    })


class LLMDirectorTests(unittest.TestCase):
    def test_valid_choice_replaces_only_that_decision(self):
        director = LLMDirector(StubDirectorProvider(_ok("research:weapons")))
        result = director.choose({"turn": 1}, _menu())
        ids = [row["option_id"] for row in result.choices]
        self.assertIn("research:weapons", ids)
        self.assertIn("front:1:hold", ids)
        self.assertEqual(result.notes_for_next_turn, "watch the border")
        self.assertEqual(result.fallbacks[0]["option_id"], "front:1:hold")

    def test_invalid_id_keeps_the_engine_choice_for_that_decision(self):
        director = LLMDirector(StubDirectorProvider(_ok("research:packets")))
        result = director.choose({"turn": 1}, _menu())
        ids = [row["option_id"] for row in result.choices]
        self.assertIn("research:energy", ids)
        self.assertTrue(any("invalid id" in line for line in result.log))

    def test_malformed_json_falls_back_to_every_engine_choice(self):
        director = LLMDirector(StubDirectorProvider("not json"))
        result = director.choose({"turn": 1}, _menu())
        self.assertEqual(
            [row["option_id"] for row in result.choices],
            ["research:energy", "front:1:hold"],
        )
        self.assertTrue(any("malformed JSON" in line for line in result.log))

    def test_provider_error_falls_back_and_records_the_reason(self):
        director = LLMDirector(StubDirectorProvider(error=DirectorError("timeout")))
        result = director.choose({"turn": 1}, _menu())
        self.assertEqual(result.strategic_posture, "rule_engine")
        self.assertIn("timeout", result.fallbacks[0]["reason"])


if __name__ == "__main__":
    unittest.main()
