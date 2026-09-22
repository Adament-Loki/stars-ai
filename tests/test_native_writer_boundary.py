"""Phase 7 contracts for the semantic/native serialization boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

import stars_ai.native.x_writer as writer
from stars_ai.models import GameState, OrderSet, RaceProfile, Tech


def test_native_writer_exposes_semantic_input_api_without_ai_dependencies():
    source = Path(writer.__file__).read_text(encoding="utf-8")

    assert "def write_native_orders(" in source
    assert "def write_ai_turn(" not in source
    for forbidden_import in (
        "from stars_ai.agent import StarsAgent",
        "from stars_ai.memory import AgentMemory",
        "from stars_ai.adapters.native_core_adapter import NativeCoreTurnAdapter",
        "from stars_ai.reporting import",
    ):
        assert forbidden_import not in source


def test_native_writer_rejects_orders_for_a_different_player_before_file_io(tmp_path):
    state = GameState("BOUNDARY", 2400, 1, RaceProfile(), Tech(), [], [])
    orders = OrderSet("BOUNDARY", 2400, 2)

    with pytest.raises(ValueError, match="does not match GameState player"):
        writer.write_native_orders(
            game_state=state,
            orders=orders,
            template_x_path=tmp_path / "missing.x1",
            output_x_path=tmp_path / "out.x1",
        )
