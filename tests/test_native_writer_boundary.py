"""Phase 7 contracts for the semantic/native serialization boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

import stars_ai.native.x_writer as writer
from stars_ai.adapters.native_core_adapter import NativeCoreTurnAdapter
from stars_ai.adapters.stars_native import read_blocks
from stars_ai.models import GameState, OrderSet, RaceProfile, Tech
from stars_ai.turn_execution import plan_native_ai_turn

from tests.helpers.resources import test_resource as resource_path


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


def test_native_writer_uses_adapter_m_provenance_for_current_x_header(tmp_path):
    root = resource_path("sandboxes", "sandbox-native-reference")
    m_path = root / "GAME.m1"
    xy_path = root / "GAME.xy"
    state = NativeCoreTurnAdapter(xy_path=xy_path).read_state(m_path, player_id=1)
    planned = plan_native_ai_turn(
        player_id=1,
        m_path=m_path,
        xy_path=xy_path,
    )
    output = tmp_path / "GAME.x1"

    result = writer.write_native_orders(
        game_state=state,
        orders=planned.decision.orders,
        template_x_path=root / "backup" / "GAME.x1",
        output_x_path=output,
    )

    m_header, _, _ = read_blocks(m_path)
    x_header, _, _ = read_blocks(output)
    assert output.is_file()
    assert result.output_x == str(output)
    assert x_header.game_id == m_header.game_id
    assert x_header.turn == m_header.turn
    assert x_header.player_index == 0
