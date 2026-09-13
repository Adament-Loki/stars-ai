from __future__ import annotations
from pathlib import Path
import json

import stars_ai.turn_archive as turn_archive_module
from stars_ai.turn_archive import (
    archive_approved_next_turn_actions,
    archive_turn_phase,
    verify_turn_archive,
)


def test_archive_captures_game_templates_memory_and_logs(tmp_path: Path):
    game=tmp_path/"game"; game.mkdir()
    logs=tmp_path/"logs"; logs.mkdir()
    templates=tmp_path/"templates"; templates.mkdir()
    state=tmp_path/"state"; state.mkdir()
    archive=logs/"turn-archive"

    (game/"GAME.hst").write_bytes(b"hst")
    (game/"GAME.xy").write_bytes(b"xy")
    (game/"GAME.m1").write_bytes(b"m1")
    (game/"GAME.x1").write_bytes(b"x1")
    (game/"OTHER.m1").write_bytes(b"ignore")
    (templates/"template.x1").write_bytes(b"template")
    (templates/"templates.json").write_text("{}",encoding="utf-8")
    (state/"player-01-memory.json").write_text('{"k":1}',encoding="utf-8")
    (state/"player-01-memory.pending.json").write_text('{"k":2}',encoding="utf-8")
    (logs/"turn-003-player-01-decision-native.json").write_text("{}",encoding="utf-8")

    dest=archive_turn_phase(
        archive,turn_tag="turn-003",phase="10-pre-host",
        game_dir=game,basename="GAME",logs_root=logs,
        templates_root=templates,ai_state_root=state,
        config={"host_password":"secret","turns":50},
        metadata={"host_success":False},
    )

    assert (dest/"game"/"GAME.x1").read_bytes()==b"x1"
    assert not (dest/"game"/"OTHER.m1").exists()
    assert (dest/"x-templates"/"template.x1").exists()
    assert (dest/"ai-state"/"player-01-memory.pending.json").exists()
    assert (dest/"logs"/"turn-003-player-01-decision-native.json").exists()
    manifest=json.loads((dest/"manifest.json").read_text(encoding="utf-8"))
    assert manifest["config"]["host_password"]=="<redacted>"
    assert manifest["metadata"]["host_success"] is False
    check=verify_turn_archive(dest)
    assert check["ok"],check


def test_archive_never_overwrites_existing_phase(tmp_path: Path):
    game=tmp_path/"game"; game.mkdir()
    (game/"GAME.hst").write_bytes(b"one")
    root=tmp_path/"archive"
    first=archive_turn_phase(root,turn_tag="turn-001",phase="00-pre-write",game_dir=game,basename="GAME")
    (game/"GAME.hst").write_bytes(b"two")
    second=archive_turn_phase(root,turn_tag="turn-001",phase="00-pre-write",game_dir=game,basename="GAME")
    assert first!=second
    assert first.name=="00-pre-write"
    assert second.name=="00-pre-write-02"
    assert (first/"game"/"GAME.hst").read_bytes()==b"one"
    assert (second/"game"/"GAME.hst").read_bytes()==b"two"


def test_end_of_turn_action_bundle_uses_the_next_decision_cycle_reports(tmp_path: Path):
    game=tmp_path/"game"; game.mkdir()
    logs=tmp_path/"logs"; logs.mkdir()
    archive=logs/"turn-archive"
    (game/"GAME.hst").write_bytes(b"hst")
    (game/"GAME.xy").write_bytes(b"xy")
    (game/"GAME.m1").write_bytes(b"m1")
    (game/"GAME.x1").write_bytes(b"next-order")
    (logs/"turn-011-player-01-DECISION_REPORT.txt").write_text(
        "Fleet #4 - MOVE -> Himshaw",encoding="utf-8"
    )

    dest=archive_approved_next_turn_actions(
        archive,turn_tag="turn-010",decision_turn_tag="turn-011",
        game_dir=game,basename="GAME",logs_root=logs,
        metadata={"action_status":"APPROVED_FOR_NEXT_HOST"},
    )

    summary=(dest/"END_OF_TURN_APPROVED_ACTIONS.txt").read_text(encoding="utf-8")
    assert "Committed state reviewed: turn-010" in summary
    assert "Decision source cycle: turn-011" in summary
    assert "Fleet #4 - MOVE -> Himshaw" in summary
    assert (dest/"orders"/"GAME.x1").read_bytes()==b"next-order"
    assert (dest/"reports"/"turn-011-player-01-DECISION_REPORT.txt").exists()
    assert not (dest/"game").exists()
    assert verify_turn_archive(dest)["ok"]


def test_atomic_json_write_retries_a_transient_windows_replace_lock(tmp_path: Path, monkeypatch):
    target=tmp_path/"turn.json"
    real_replace=turn_archive_module.os.replace
    attempts=0

    def flaky_replace(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError(5, "Access is denied", str(destination))
        return real_replace(source, destination)

    monkeypatch.setattr(turn_archive_module.os, "replace", flaky_replace)
    monkeypatch.setattr(turn_archive_module.time, "sleep", lambda _seconds: None)

    turn_archive_module._atomic_json_write(target, {"ok": True})

    assert attempts == 2
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert not list(tmp_path.glob("*.tmp"))
