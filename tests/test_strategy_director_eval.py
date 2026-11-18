"""Advise-mode replay scores agreement without changing a saved game."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from stars_ai.llm_director import StubDirectorProvider
from stars_ai.strategy_director_eval import replay_advise

SANDBOX = Path("tests/TestResource/sandboxes/sandbox-native-reference")


class _Agree(StubDirectorProvider):
    def complete(self, prompt: str) -> str:
        menu = json.loads(prompt.split("\n", 1)[1])["menu"]
        return json.dumps({
            "strategic_posture": "engine",
            "choices": [{"option_id": option_id, "reason": "agree"} for option_id in menu["engine_choices"]],
            "notes_for_next_turn": "",
        })


class StrategyDirectorEvalTests(unittest.TestCase):
    def test_advise_replay_reports_agreement_latency_and_cost(self):
        m_path = SANDBOX / "GAME.m1"
        xy_path = SANDBOX / "GAME.xy"
        if not m_path.is_file() or not xy_path.is_file():
            self.skipTest("sandbox turn files are not in this checkout")
        report = replay_advise([{"m": m_path, "xy": xy_path}], _Agree())
        self.assertEqual(report["turns"], 1)
        self.assertEqual(report["agreement_rate"], 1.0)
        self.assertEqual(report["invalid_response_rate"], 0.0)
        self.assertGreaterEqual(report["mean_latency_seconds"], 0.0)
        self.assertGreater(report["total_token_estimate"], 0)
        self.assertGreaterEqual(report["total_estimated_cost_usd"], 0.0)
        self.assertEqual(report["decide_mode_host_comparison"], "not run")

    def test_malformed_replay_counts_as_an_invalid_response(self):
        m_path = SANDBOX / "GAME.m1"
        xy_path = SANDBOX / "GAME.xy"
        if not m_path.is_file() or not xy_path.is_file():
            self.skipTest("sandbox turn files are not in this checkout")
        report = replay_advise([{"m": m_path, "xy": xy_path}], StubDirectorProvider("nope"))
        self.assertEqual(report["invalid_response_rate"], 1.0)
        self.assertEqual(report["agreement_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
