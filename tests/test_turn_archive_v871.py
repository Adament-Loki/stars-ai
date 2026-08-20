from __future__ import annotations
from pathlib import Path
import json

from stars_ai.turn_archive import archive_turn_phase, verify_turn_archive


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
