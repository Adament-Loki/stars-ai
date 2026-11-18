"""Replay saved turns in advise mode and score the director against the engine.

This harness does not host a game and does not write orders. A decide-mode
match against the existing AI still has to be run on the Windows worker.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from stars_ai.adapters.native_core_adapter import NativeCoreTurnAdapter
from stars_ai.llm_director import DirectorProvider
from stars_ai.strategy_digest import estimate_tokens
from stars_ai.strategy_director_session import consult_strategy_director
from stars_ai.strategy_guardrails import _COST_PER_1K_TOKENS


def replay_advise(
    turns: list[dict[str, Any]],
    provider: DirectorProvider,
    *,
    player_id: int = 1,
) -> dict[str, Any]:
    """Run each saved turn in advise mode and summarize agreement and cost."""
    rows = []
    for turn in turns:
        state = NativeCoreTurnAdapter(xy_path=Path(turn["xy"])).read_state(Path(turn["m"]), player_id)
        state.native["llm_director_mode"] = "advise"
        state.native["llm_director_provider"] = provider
        started = time.perf_counter()
        record = consult_strategy_director(state, _empty_orders(state))
        elapsed = time.perf_counter() - started
        choices = [row["option_id"] for row in (record or {}).get("decision", {}).get("choices", [])]
        engine = set((record or {}).get("engine_choices") or [])
        log = (record or {}).get("decision", {}).get("log") or []
        invalid = any("invalid" in line or "malformed" in line or "Error" in line for line in log)
        agreed = sum(1 for choice in choices if choice in engine)
        tokens = estimate_tokens({"prompt": (record or {}).get("prompt", "")})
        rows.append({
            "turn": (state.native.get("header") or {}).get("turn"),
            "agreed": agreed,
            "decisions": max(1, len(choices)),
            "invalid": invalid,
            "latency_seconds": round(elapsed, 4),
            "token_estimate": tokens,
            "estimated_cost_usd": round(tokens / 1000.0 * _COST_PER_1K_TOKENS, 6),
        })
    decisions = sum(row["decisions"] for row in rows)
    agreed = sum(row["agreed"] for row in rows)
    invalid_turns = sum(1 for row in rows if row["invalid"])
    return {
        "turns": len(rows),
        "agreement_rate": round(agreed / decisions, 4) if decisions else 0.0,
        "invalid_response_rate": round(invalid_turns / len(rows), 4) if rows else 0.0,
        "mean_latency_seconds": round(sum(row["latency_seconds"] for row in rows) / len(rows), 4) if rows else 0.0,
        "total_token_estimate": sum(row["token_estimate"] for row in rows),
        "total_estimated_cost_usd": round(sum(row["estimated_cost_usd"] for row in rows), 6),
        "rows": rows,
        "decide_mode_host_comparison": "not run",
    }


def _empty_orders(state: Any):
    from stars_ai.core.models import OrderSet
    return OrderSet(state.game_name, state.year, state.player_id)
