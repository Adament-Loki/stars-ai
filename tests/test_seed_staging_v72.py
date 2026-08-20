import json
import os
from pathlib import Path

import pytest

from stars_ai.adapters.stars_native import NativeBlock
from stars_ai.native.x_writer import (
    FILEHASH_CANONICAL_TAIL,
    _encode_blocks,
)
from stars_ai.windows_autohost import (
    LiveGameValidationError,
    HistorySyncError,
    NoopOrderBridge,
    SeedValidationError,
    WindowsAutoHostConfig,
    _history_sync_barrier,
    _history_sync_report,
    _sha256,
    _snapshot,
    _stage_seed_game,
    _stars_execution_dir,
    _validate_live_game,
    _validate_seed_game,
    _write_bootstrap_snapshot,
    run_50_turn_game,
)


def _header(*, game_id=0x12345678, turn=0, player=1, file_type=3, submitted=False):
    data=bytearray(16)
    data[:4]=b"J3J3"
    data[4:8]=int(game_id).to_bytes(4,"little")
    data[8:10]=(0x2A60).to_bytes(2,"little")
    data[10:12]=int(turn).to_bytes(2,"little")
    data[12:14]=((321<<5)|(int(player)-1)).to_bytes(2,"little")
    data[14]=int(file_type)
    data[15]=1 if submitted else 0
    return NativeBlock(8,16,bytes(data))


def _write_native(path:Path, header:NativeBlock, blocks:list[NativeBlock]|None=None):
    payload=list(blocks or [])+[NativeBlock(0,0,b"")]
    path.write_bytes(_encode_blocks(header,payload))


def _make_seed(root:Path, *, players=(1,2), basename="GAME") -> Path:
    root.mkdir()
    _write_native(root/f"{basename}.hst",_header(file_type=2))
    _write_native(root/f"{basename}.xy",_header(file_type=0))
    for player in players:
        _write_native(
            root/f"{basename}.m{player}",
            _header(player=player,file_type=3),
        )
        _write_native(
            root/f"{basename}.h{player}",
            _header(player=player,file_type=4),
            [NativeBlock(32,4,b"\0\0\0\0")],
        )
        filehash=NativeBlock(9,17,b"\0\0"+FILEHASH_CANONICAL_TAIL)
        _write_native(
            root/f"{basename}.x{player}",
            _header(player=player,file_type=1,submitted=True),
            [filehash],
        )
    return root


def _cfg(tmp_path:Path, seed:Path) -> WindowsAutoHostConfig:
    execution=tmp_path/"stars"
    execution.mkdir(exist_ok=True)
    exe=execution/"stars!.exe"
    exe.write_bytes(b"exe")
    return WindowsAutoHostConfig(
        stars_exe=str(exe),
        seed_dir=str(seed),
        output_dir=str(tmp_path/"output"),
        player_ids=[1,2],
        turns=1,
    )


def _set_live_turn(game:Path, *, players=(1,2), turn=5, basename="GAME"):
    _write_native(game/f"{basename}.hst",_header(turn=turn,file_type=2))
    for player in players:
        _write_native(
            game/f"{basename}.m{player}",
            _header(turn=turn,player=player,file_type=3),
        )
        x_path=game/f"{basename}.x{player}"
        if x_path.exists():
            x_path.unlink()


def test_valid_seed_stages_to_stars_directory_and_remains_immutable(tmp_path):
    seed=_make_seed(tmp_path/"seed")
    cfg=_cfg(tmp_path,seed)
    game=_stars_execution_dir(cfg)
    (game/"GAME.x9").write_bytes(b"stale")
    (game/"OTHER.hst").write_bytes(b"unrelated game")
    (game/"stars.ini").write_text("keep",encoding="utf-8")
    (game/"support.dll").write_bytes(b"keep")
    before={p.name:_sha256(p) for p in seed.iterdir() if p.is_file()}

    validated=_validate_seed_game(cfg)
    staged=_stage_seed_game(validated,game)
    snapshot=_write_bootstrap_snapshot(validated,game,Path(cfg.output_dir))

    assert validated.basename=="GAME"
    assert {p.name for p in staged}==set(before)
    assert {p.name:_sha256(p) for p in seed.iterdir() if p.is_file()}==before
    assert not (game/"GAME.x9").exists()
    assert (game/"OTHER.hst").read_bytes()==b"unrelated game"
    assert (game/"stars.ini").read_text(encoding="utf-8")=="keep"
    assert (game/"support.dll").read_bytes()==b"keep"
    assert (snapshot/"manifest.json").exists()
    assert (snapshot/"GAME.x1").exists()


@pytest.mark.parametrize("missing",["GAME.x1","GAME.x2"])
def test_missing_initial_x_fails_before_live_staging(tmp_path,missing):
    seed=_make_seed(tmp_path/"seed")
    (seed/missing).unlink()
    cfg=_cfg(tmp_path,seed)
    game=_stars_execution_dir(cfg)
    stale=game/"GAME.hst"
    stale.write_bytes(b"live must remain unchanged")

    with pytest.raises(SeedValidationError,match="expected exactly one file"):
        run_50_turn_game(cfg,NoopOrderBridge())

    assert stale.read_bytes()==b"live must remain unchanged"
    assert not Path(cfg.output_dir).exists()


@pytest.mark.parametrize(
    ("wrong_game","wrong_player","message"),
    [
        (True,False,"game id"),
        (False,True,"declares player"),
    ],
)
def test_mismatched_x_game_or_player_fails_closed(
    tmp_path,wrong_game,wrong_player,message
):
    seed=_make_seed(tmp_path/"seed")
    filehash=NativeBlock(9,17,b"\0\0"+FILEHASH_CANONICAL_TAIL)
    _write_native(
        seed/"GAME.x1",
        _header(
            game_id=0x99999999 if wrong_game else 0x12345678,
            player=2 if wrong_player else 1,
            file_type=1,
            submitted=True,
        ),
        [filehash],
    )
    cfg=_cfg(tmp_path,seed)

    with pytest.raises(SeedValidationError,match=message):
        _validate_seed_game(cfg)


def test_multiple_game_basenames_fail_closed(tmp_path):
    seed=_make_seed(tmp_path/"seed")
    _write_native(seed/"OTHER.hst",_header(file_type=2))
    cfg=_cfg(tmp_path,seed)
    with pytest.raises(SeedValidationError,match="exactly one Stars! game basename"):
        _validate_seed_game(cfg)


def test_live_path_is_always_stars_executable_parent(tmp_path):
    seed=_make_seed(tmp_path/"seed")
    cfg=_cfg(tmp_path,seed)
    assert _stars_execution_dir(cfg)==Path(cfg.stars_exe).resolve().parent
    assert _stars_execution_dir(cfg)!=seed.resolve()


def test_play_on_validates_and_preserves_current_live_game(tmp_path,monkeypatch):
    seed=_make_seed(tmp_path/"seed")
    cfg=_cfg(tmp_path,seed)
    cfg.play_on=True
    cfg.turns=0
    cfg.x_template_dir=str(tmp_path/"templates")
    cfg.ai_state_dir=str(tmp_path/"ai-state")
    validated=_validate_seed_game(cfg)
    game=_stars_execution_dir(cfg)
    _stage_seed_game(validated,game)
    _set_live_turn(game,turn=5)
    before={
        path.name:_sha256(path)
        for path in game.iterdir()
        if path.is_file() and path.name.startswith("GAME.")
    }

    live=_validate_live_game(cfg,validated)
    assert live.turn==5
    assert live.game_id==validated.game_id

    monkeypatch.setattr(
        "stars_ai.windows_autohost._stage_seed_game",
        lambda *_args,**_kwargs: pytest.fail("play_on must not stage the seed"),
    )
    results=run_50_turn_game(cfg,NoopOrderBridge())

    assert results==[]
    after={
        path.name:_sha256(path)
        for path in game.iterdir()
        if path.is_file() and path.name.startswith("GAME.")
    }
    assert after==before
    manifest=(Path(cfg.output_dir)/"bootstrap"/"manifest.json").read_text(
        encoding="utf-8"
    )
    assert '"mode": "play_on"' in manifest
    assert '"starting_turn": 5' in manifest
    assert (Path(cfg.x_template_dir)/"template.x1").exists()
    assert (Path(cfg.x_template_dir)/"template.x2").exists()


def test_play_on_rejects_mismatched_live_turn_before_mutation(tmp_path):
    seed=_make_seed(tmp_path/"seed")
    cfg=_cfg(tmp_path,seed)
    cfg.play_on=True
    validated=_validate_seed_game(cfg)
    game=_stars_execution_dir(cfg)
    _stage_seed_game(validated,game)
    _set_live_turn(game,turn=5)
    _write_native(game/"GAME.m2",_header(turn=4,player=2,file_type=3))
    before=_sha256(game/"GAME.m2")

    with pytest.raises(
        LiveGameValidationError,
        match=r"GAME\.m2.*turn 4.*live HST turn 5",
    ):
        run_50_turn_game(cfg,NoopOrderBridge())

    assert _sha256(game/"GAME.m2")==before
    assert not Path(cfg.output_dir).exists()


def test_history_sync_barrier_rejects_stale_h_before_x_generation(tmp_path):
    seed=_make_seed(tmp_path/"seed")
    cfg=_cfg(tmp_path,seed)
    validated=_validate_seed_game(cfg)
    game=_stars_execution_dir(cfg)
    _stage_seed_game(validated,game)
    for player in cfg.player_ids:
        (game/f"GAME.x{player}").unlink()

    m_path=game/"GAME.m1"
    h_path=game/"GAME.h1"
    current_planet=NativeBlock(14,4,b"\x07\xf8\x01\x01")
    history_planet=NativeBlock(14,6,b"\x07\xf8\x01\x01\x01\x00")
    _write_native(m_path,_header(turn=1,player=1,file_type=3),[current_planet])
    _write_native(
        h_path,_header(turn=0,player=1,file_type=4),
        [NativeBlock(32,4,b"\x01\x00\x00\x00"),history_planet],
    )
    assert _history_sync_report(game,"GAME",cfg.player_ids)["ready"]

    stale_planet=NativeBlock(14,6,b"\x07\xf8\x01\x01\x00\x00")
    _write_native(
        h_path,_header(turn=0,player=1,file_type=4),
        [NativeBlock(32,4,b"\x01\x00\x00\x00"),stale_planet],
    )
    logs=tmp_path/"logs"
    logs.mkdir()
    with pytest.raises(HistorySyncError,match="AUTOMATIC HISTORY MERGE REQUIRED"):
        _history_sync_barrier(cfg,game,"GAME",logs,1)

    report=json.loads((logs/"turn-001-HISTORY_SYNC.json").read_text(encoding="utf-8"))
    assert report["status"]=="AUTOMATIC_HISTORY_MERGE_REQUIRED"
    assert "stale planet observations" in report["players"][0]["errors"][0]
    assert not (game/"GAME.x1").exists()
    assert not (game/"GAME.x2").exists()


def test_native_snapshot_includes_h_files_and_refuses_overwrite(tmp_path):
    seed=_make_seed(tmp_path/"seed")
    cfg=_cfg(tmp_path,seed)
    validated=_validate_seed_game(cfg)
    game=_stars_execution_dir(cfg)
    _stage_seed_game(validated,game)
    dest=tmp_path/"logs"/"native"/"turn-001-post-host"

    _snapshot(
        game,dest,basename="GAME",
        metadata={"phase":"post_host","host_success":True},
    )

    manifest=json.loads((dest/"manifest.json").read_text(encoding="utf-8"))
    assert {"GAME.h1","GAME.h2","GAME.hst","GAME.m1","GAME.m2","GAME.xy"} <= set(
        manifest["snapshot_files"]
    )
    assert manifest["files"]["GAME.h1"]["sha256"]==_sha256(dest/"GAME.h1")
    assert manifest["phase"]=="post_host"
    with pytest.raises(FileExistsError,match="immutable native snapshot"):
        _snapshot(game,dest,basename="GAME")
