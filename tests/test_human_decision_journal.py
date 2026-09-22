"""Phase 6 contracts for flat player journals and verbose report storage."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from stars_ai.ai.decision_journal import DecisionJournal
from stars_ai.autohost.bridges import IntegratedNativeOrderBridge
from stars_ai.models import Fleet, GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.reporting import (
    DecisionJournalReporter,
    build_turn_report_model,
    render_player_journal,
)


def _state(year: int = 2400) -> GameState:
    return GameState(
        "JOURNAL",
        year,
        1,
        RaceProfile(),
        Tech(),
        [
            Planet(0, "Home", Position(0, 0), owner=1, population=100_000, factories=12, mines=8),
            Planet(1, "Vega", Position(64, 0), observed=False),
        ],
        [Fleet(3, "Scout 3", 1, Position(0, 0), role="scout")],
        native={
            "research_strategy": {"capability_name": "Propulsion 3", "reason": "Expansion range."},
            "command_outcomes": [{
                "kind": "move_fleet",
                "description": "Scout 3 reaches Home",
                "status": "COMPLETED",
                "message": "COMPLETED - Scout 3 reached Home.",
            }],
        },
    )


def _model(year: int, turn: int):
    state = _state(year)
    orders = OrderSet("JOURNAL", year, 1)
    orders.add(
        "move_fleet",
        {"fleet_id": 3, "destination_planet_id": 1, "mission": "scan", "warp": 8},
        "Scout 3 investigates the highest-value known frontier world.",
        priority=80,
    )
    journal = DecisionJournal(player_id=1, turn=turn, persona="Expansionist")
    journal.record(
        domain="strategy",
        action="Strategic phase: expansion",
        reason="Frontier capacity remains available.",
        target="expansion",
        evidence={"open_frontier_fraction": 0.4},
    )
    journal.record_order("exploration", state, orders.orders[0])
    journal.record_fleet_intent(state, {
        "fleet_id": 3,
        "fleet_name": "Scout 3",
        "role": "scout",
        "action": "RECON",
        "reason": "Vega is the best reachable frontier target.",
        "destination_planet_id": 1,
        "colony_candidates": [{"planet_name": "Vega", "score": 84, "selection_basis": "selected"}],
    })
    agent = SimpleNamespace(
        persona=SimpleNamespace(name="Expansionist"),
        last_plan=SimpleNamespace(
            persona_name="Expansionist",
            phase="expansion",
            phase_reason="Frontier capacity remains available.",
            objective_weights={"expand": 1.5, "research": 1.0},
            phase_assessment={"open_frontier_fraction": 0.4},
            notes=("Expand toward Vega.",),
        ),
        fleet_intents=[{
            "fleet_id": 3,
            "fleet_name": "Scout 3",
            "role": "scout",
            "action": "RECON",
            "reason": "Vega is the best reachable frontier target.",
            "destination_planet_id": 1,
        }],
        decision_journal=journal,
    )
    return build_turn_report_model(state, orders, agent, [{"kind": "move_fleet", "payload": {}}], [], []).with_turn(turn)


def test_root_journal_is_cumulative_idempotent_and_consolidated(tmp_path):
    reporter = DecisionJournalReporter(tmp_path)
    first = _model(2400, 1)
    second = _model(2401, 2)

    reporter.write_turn(first)
    reporter.write_turn(second)
    reporter.write_turn(second)  # same stable player/turn/year identity

    journal = (tmp_path / "1.md").read_text(encoding="utf-8")
    assert "# Player 1 — AI Decision Journal" in journal
    assert journal.index("# Turn 2 — Year 2401") < journal.index("# Turn 1 — Year 2400")
    assert journal.count("# Turn 2 — Year 2401") == 1
    for heading in (
        "## Overview",
        "## Empire State",
        "## Strategy",
        "## Important Decisions",
        "## Fleet Activity",
        "## Final Semantic Orders",
        "## Native Execution Summary",
        "## Previous Turn Results",
        "## Watch Next Turn",
    ):
        assert heading in journal
    assert "Scout 3" in journal

    turn_dir = tmp_path / "verbose" / "turn-002-year-2401" / "player-01"
    for filename in (
        "state.md", "strategy.md", "decisions.md", "fleets.md", "orders.md",
        "native-audit.md", "outcome.md", "turn.json",
    ):
        assert (turn_dir / filename).exists()
    assert not (tmp_path / "turn-002-year-2401").exists()
    assert json.loads((turn_dir / "turn.json").read_text(encoding="utf-8")) == second.to_dict()

    summary = (tmp_path / "TURN_SUMMARY.md").read_text(encoding="utf-8")
    assert "# Turn 2 — Year 2401" in summary
    assert "| 1 | expansion |" in summary


def test_markdown_and_json_use_the_same_stable_report_model(tmp_path):
    report = _model(2400, 1)
    reporter = DecisionJournalReporter(tmp_path)
    reporter.write_turn(report)

    stored = json.loads((tmp_path / "verbose" / "turn-001-year-2400" / "player-01" / "turn.json").read_text(encoding="utf-8"))
    restored = type(report).from_dict(stored)
    assert restored.to_dict() == report.to_dict()
    assert render_player_journal([report]) == render_player_journal([restored])


def test_bridge_writes_flat_report_from_writer_structured_model(monkeypatch, tmp_path):
    model = _model(2400, 1)
    result = SimpleNamespace(year=2400, emitted=[], skipped=[])
    execution = SimpleNamespace(native_result=result, report_model=model)
    monkeypatch.setattr("stars_ai.autohost.bridges.execute_native_ai_turn", lambda **_: execution)
    template = tmp_path / "GAME.x1"
    template.write_bytes(b"template")
    bridge = IntegratedNativeOrderBridge(report_root=tmp_path)
    bridge.turn_tag = "turn-001"

    bridge.create_x_file(
        player_id=1,
        m_path=tmp_path / "GAME.m1",
        xy_path=tmp_path / "GAME.xy",
        existing_x_path=template,
        output_x_path=tmp_path / "GENERATED.x1",
        turn_dir=tmp_path / "logs",
    )

    assert (tmp_path / "1.md").exists()
    assert (tmp_path / "TURN_SUMMARY.md").exists()
    assert (tmp_path / "verbose" / "turn-001-year-2400" / "player-01" / "turn.json").exists()


def test_missing_optional_data_does_not_fabricate_warning_or_threat(tmp_path):
    report = _model(2400, 1)
    report = type(report)(
        **{**report.to_dict(), "primary_threat": None, "warnings": [], "previous_outcomes": []}
    )
    DecisionJournalReporter(tmp_path).write_turn(report)
    text = (tmp_path / "1.md").read_text(encoding="utf-8")

    assert "**Primary Threat:**" not in text
    assert "No prior emitted action had an observable result this turn." in text
    assert "No warning was recorded from the available diagnostics." in text
