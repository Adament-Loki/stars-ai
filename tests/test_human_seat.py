import shutil
from pathlib import Path

import pytest

from stars_ai.adapters.stars_native import (
    clear_computer_control,
    player_is_computer,
    read_blocks,
    write_blocks,
)
from stars_ai.autohost.models import WindowsAutoHostConfig
from stars_ai.windows_autohost import _human_order_status, _open_human_turn


SEED = Path("playtests/seed/5Comps11Total/GAME.hst")


def _config(**overrides):
    values = dict(
        stars_exe="stars.exe",
        seed_dir="seed",
        output_dir="out",
        turns=1,
        player_ids=[1, 2, 3, 4],
    )
    values.update(overrides)
    return WindowsAutoHostConfig(**values)


def test_a_seat_cannot_be_both_ai_and_human():
    with pytest.raises(ValueError, match="both an AI player and a human"):
        _config(human_player_ids=[4])


def test_host_file_round_trip_is_byte_identical(tmp_path: Path):
    staged = tmp_path / "GAME.hst"
    shutil.copy2(SEED, staged)
    _header, blocks, extra = read_blocks(staged)
    assert not extra
    write_blocks(staged, blocks)
    assert staged.read_bytes() == SEED.read_bytes()


def test_clearing_computer_control_only_changes_the_named_seat(tmp_path: Path):
    staged = tmp_path / "GAME.hst"
    shutil.copy2(SEED, staged)
    changed = clear_computer_control(staged, [6])
    assert changed == [6]
    _header, blocks, _extra = read_blocks(staged)
    players = {block.data[0] + 1: block.data for block in blocks if block.type_id == 6}
    assert player_is_computer(players[6]) is False
    assert player_is_computer(players[5]) is True
    assert player_is_computer(players[7]) is True
    assert player_is_computer(players[1]) is False
    assert clear_computer_control(staged, [6]) == []


def test_human_order_status_requires_this_turn_submitted(tmp_path: Path):
    orders = tmp_path / "GAME.x5"
    assert _human_order_status(orders, 0) == (False, "GAME.x5 is not saved yet")
    _header, blocks, _extra = read_blocks(SEED)
    write_blocks(orders, blocks)
    ready, reason = _human_order_status(orders, 0)
    assert ready is False
    assert "not submitted" in reason
    ready, reason = _human_order_status(orders, 3)
    assert ready is False
    assert "turn 0" in reason
    _header, blocks, _extra = read_blocks(orders)
    submitted = []
    for block in blocks:
        if block.type_id == 8:
            data = bytearray(block.data)
            data[15] |= 0x01
            block = type(block)(block.type_id, len(data), bytes(data))
        submitted.append(block)
    write_blocks(orders, submitted)
    assert _human_order_status(orders, 0) == (True, "submitted")


def test_opening_a_human_turn_starts_stars_on_the_m_file(tmp_path: Path, monkeypatch):
    game = tmp_path / "live"
    game.mkdir()
    (game / "GAME.m1").write_bytes(b"turn")
    cfg = _config(
        stars_exe=str(game / "starsjrc4.exe"),
        player_ids=[2, 3, 4, 5],
        human_player_ids=[1],
    )
    launched: dict = {}

    def fake_popen(cmd, **kwargs):
        launched["cmd"] = cmd
        launched["cwd"] = kwargs["cwd"]
        return None

    monkeypatch.setattr("stars_ai.windows_autohost.subprocess.Popen", fake_popen)
    opened = _open_human_turn(cfg, game, "GAME", 1)
    assert opened.name == "GAME.m1"
    assert launched["cmd"] == [cfg.stars_exe, "GAME.m1"]
    assert "-g" not in launched["cmd"]
    assert launched["cwd"] == str(game)
