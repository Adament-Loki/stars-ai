"""Human-seat wait timeout: config, CLI, and fail-closed expiry.

Time is a fake clock. The game directory is an isolated temp folder, and the
Stars! process check is stubbed so the wait never touches a live client.
"""

from __future__ import annotations

import inspect
import json
import shutil
from pathlib import Path

import pytest

from stars_ai.adapters.stars_native import read_blocks, write_blocks
from stars_ai.autohost.models import (
    DEFAULT_HUMAN_TURN_TIMEOUT_SECONDS,
    HumanTurnTimeoutError,
    WindowsAutoHostConfig,
)
from stars_ai.autoplay_cli import apply_cli_overrides, build_parser, main
from stars_ai.autohost import load_windows_autohost_config
from stars_ai.windows_autohost import _wait_for_human_orders, run_50_turn_game


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "playtests" / "seed" / "5Comps11Total" / "GAME.hst"


class Clock:
    def __init__(self) -> None:
        self.start = 10_000.0
        self.now = self.start
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(float(seconds))
        self.now += float(seconds)


def _config(**overrides) -> WindowsAutoHostConfig:
    values = dict(
        stars_exe="stars.exe",
        seed_dir="seed",
        output_dir="out",
        turns=1,
        player_ids=[2, 3, 4, 5],
        human_player_ids=[1],
        open_human_turn=False,
        host_poll_seconds=1.0,
    )
    values.update(overrides)
    return WindowsAutoHostConfig(**values)


def _write_config(path: Path, **extra) -> Path:
    payload = {
        "stars_exe": "stars.exe",
        "seed_dir": "seed",
        "output_dir": "out",
        "player_ids": [2],
        "human_player_ids": [1],
    }
    payload.update(extra)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _stage_game(tmp_path: Path) -> Path:
    game = tmp_path / "live"
    game.mkdir()
    shutil.copy2(SEED, game / "GAME.hst")
    return game


def _write_orders(path: Path, *, submitted: bool) -> None:
    _header, blocks, _extra = read_blocks(SEED)
    if not submitted:
        write_blocks(path, blocks)
        return
    updated = []
    for block in blocks:
        if block.type_id == 8:
            data = bytearray(block.data)
            data[15] |= 0x01
            block = type(block)(block.type_id, len(data), bytes(data))
        updated.append(block)
    write_blocks(path, updated)


def _install_clock(monkeypatch, clock: Clock | None = None) -> Clock:
    clock = clock or Clock()
    monkeypatch.setattr("stars_ai.windows_autohost.time.time", clock.time)
    monkeypatch.setattr("stars_ai.windows_autohost.time.sleep", clock.sleep)
    monkeypatch.setattr(
        "stars_ai.windows_autohost._stars_client_running",
        lambda _exe: False,
    )
    return clock


def test_default_human_wait_is_one_hour():
    cfg = WindowsAutoHostConfig(stars_exe="stars.exe", seed_dir="seed", output_dir="out")
    assert cfg.human_turn_timeout_seconds == DEFAULT_HUMAN_TURN_TIMEOUT_SECONDS == 3600


def test_forever_is_explicit_and_non_positive_values_are_rejected():
    assert _config(human_turn_timeout_seconds=None).human_turn_timeout_seconds is None
    assert _config(human_turn_timeout_seconds="forever").human_turn_timeout_seconds is None
    assert _config(human_turn_timeout_seconds=" Forever ").human_turn_timeout_seconds is None
    assert _config(human_turn_timeout_seconds="120").human_turn_timeout_seconds == 120
    assert _config(human_turn_timeout_seconds=120.0).human_turn_timeout_seconds == 120
    for bad in (0, -5, 1.5, True, "later", ""):
        with pytest.raises(ValueError, match="forever"):
            _config(human_turn_timeout_seconds=bad)


def test_autoplay_json_sets_the_human_wait(tmp_path: Path):
    omitted = _write_config(tmp_path / "omitted.json")
    assert load_windows_autohost_config(omitted).human_turn_timeout_seconds == 3600

    forever = _write_config(tmp_path / "forever.json", human_turn_timeout_seconds="forever")
    assert load_windows_autohost_config(forever).human_turn_timeout_seconds is None

    unlimited = _write_config(tmp_path / "null.json", human_turn_timeout_seconds=None)
    assert load_windows_autohost_config(unlimited).human_turn_timeout_seconds is None

    limited = _write_config(tmp_path / "limited.json", human_turn_timeout_seconds=90)
    assert load_windows_autohost_config(limited).human_turn_timeout_seconds == 90

    with pytest.raises(ValueError, match="forever"):
        load_windows_autohost_config(
            _write_config(tmp_path / "zero.json", human_turn_timeout_seconds=0)
        )


def test_cli_can_override_the_json_timeout(tmp_path: Path):
    path = _write_config(tmp_path / "autoplay.json", human_turn_timeout_seconds=30)
    cfg = load_windows_autohost_config(path)
    assert cfg.human_turn_timeout_seconds == 30

    forever = build_parser().parse_args([
        "--config", str(path),
        "--human-turn-timeout-seconds", "forever",
    ])
    apply_cli_overrides(cfg, forever)
    assert cfg.human_turn_timeout_seconds is None

    limited = build_parser().parse_args([
        "--config", str(path),
        "--human-turn-timeout-seconds", "45",
    ])
    apply_cli_overrides(cfg, limited)
    assert cfg.human_turn_timeout_seconds == 45


def test_cli_rejects_a_bad_timeout_before_the_host_loop(tmp_path: Path, monkeypatch):
    path = _write_config(tmp_path / "autoplay.json")
    monkeypatch.setattr(
        "sys.argv",
        ["autoplay_cli", "--config", str(path), "--human-turn-timeout-seconds", "0"],
    )

    def boom(*_args, **_kwargs):
        raise AssertionError("host loop should not start")

    monkeypatch.setattr("stars_ai.autoplay_cli.run_50_turn_game", boom)
    with pytest.raises(SystemExit, match="forever"):
        main()


def test_example_config_and_readme_document_the_fail_closed_wait():
    cfg = load_windows_autohost_config(ROOT / "autoplay-config.example.json")
    assert cfg.human_player_ids == []
    assert cfg.human_turn_timeout_seconds == 3600
    assert cfg.open_human_turn is True

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "human_turn_timeout_seconds" in readme
    assert '"forever"' in readme
    assert "does not host that turn" in readme
    assert "--human-turn-timeout-seconds" in readme


def test_no_human_seat_does_not_wait(tmp_path: Path, monkeypatch):
    clock = _install_clock(monkeypatch)
    _wait_for_human_orders(
        _config(human_player_ids=[], player_ids=[1, 2, 3, 4]),
        tmp_path,
        "GAME",
    )
    assert clock.sleeps == []


def test_wait_returns_when_orders_are_already_submitted(tmp_path: Path, monkeypatch, capsys):
    game = _stage_game(tmp_path)
    _write_orders(game / "GAME.x1", submitted=True)
    clock = _install_clock(monkeypatch)

    _wait_for_human_orders(_config(human_turn_timeout_seconds=30), game, "GAME")

    output = capsys.readouterr().out
    assert "Generating the turn" in output
    assert clock.sleeps == []
    assert (game / "GAME.x1").is_file()


def test_absent_human_times_out_without_writing_orders(tmp_path: Path, monkeypatch, capsys):
    game = _stage_game(tmp_path)
    clock = _install_clock(monkeypatch)

    with pytest.raises(HumanTurnTimeoutError, match="The turn was not hosted") as caught:
        _wait_for_human_orders(_config(human_turn_timeout_seconds=2), game, "GAME")

    assert "P1" in str(caught.value)
    assert "not saved yet" in str(caught.value)
    assert "limit 2s" in str(caught.value)
    assert not (game / "GAME.x1").exists()
    assert clock.now - clock.start == 2
    output = capsys.readouterr().out
    assert "[HUMAN] Timed out after 2s" in output
    assert "Waiting up to 2s" in output
    assert "The turn was not hosted" in output


def test_saved_but_unsubmitted_orders_still_time_out(tmp_path: Path, monkeypatch, capsys):
    game = _stage_game(tmp_path)
    _write_orders(game / "GAME.x1", submitted=False)
    _install_clock(monkeypatch)

    with pytest.raises(HumanTurnTimeoutError, match="not submitted"):
        _wait_for_human_orders(_config(human_turn_timeout_seconds=2), game, "GAME")

    assert "not submitted" in capsys.readouterr().out
    assert (game / "GAME.x1").is_file()


def test_one_missing_seat_blocks_the_turn(tmp_path: Path, monkeypatch):
    game = _stage_game(tmp_path)
    _write_orders(game / "GAME.x1", submitted=True)
    _install_clock(monkeypatch)
    cfg = _config(
        player_ids=[2, 4],
        human_player_ids=[1, 3],
        human_turn_timeout_seconds=2,
    )

    with pytest.raises(HumanTurnTimeoutError, match="P3:") as caught:
        _wait_for_human_orders(cfg, game, "GAME")

    assert "P1:" not in str(caught.value)
    assert (game / "GAME.x1").is_file()
    assert not (game / "GAME.x3").exists()


def test_timeout_is_checked_before_reopening_the_turn(tmp_path: Path, monkeypatch):
    game = _stage_game(tmp_path)
    clock = _install_clock(monkeypatch)
    opened: list[int] = []

    def fake_open(_cfg, live: Path, basename: str, seat: int) -> Path:
        opened.append(seat)
        return live / f"{basename}.m{seat}"

    monkeypatch.setattr("stars_ai.windows_autohost._open_human_turn", fake_open)

    with pytest.raises(HumanTurnTimeoutError):
        _wait_for_human_orders(
            _config(human_turn_timeout_seconds=3, open_human_turn=True),
            game,
            "GAME",
        )

    assert opened == [1]
    assert clock.now - clock.start == 3


def test_waiting_logs_elapsed_and_remaining_until_timeout(tmp_path: Path, monkeypatch, capsys):
    game = _stage_game(tmp_path)
    _install_clock(monkeypatch)
    monkeypatch.setattr("stars_ai.windows_autohost._HUMAN_WAIT_LOG_SECONDS", 1.0)

    with pytest.raises(HumanTurnTimeoutError):
        _wait_for_human_orders(
            _config(human_turn_timeout_seconds=3, host_poll_seconds=0.5),
            game,
            "GAME",
        )

    status = [
        line for line in capsys.readouterr().out.splitlines()
        if "s elapsed" in line and "Timed out" not in line
    ]
    assert len(status) >= 3
    assert any("s remaining" in line for line in status)
    assert "0s elapsed, 3s remaining" in status[0]


def test_forever_survives_a_long_clock_jump_then_accepts_orders(tmp_path: Path, monkeypatch, capsys):
    game = _stage_game(tmp_path)
    clock = _install_clock(monkeypatch)

    def sleep(seconds: float) -> None:
        clock.sleeps.append(float(seconds))
        clock.now += float(seconds)
        if len(clock.sleeps) == 1:
            clock.now += 100_000
        if len(clock.sleeps) == 2:
            _write_orders(game / "GAME.x1", submitted=True)

    monkeypatch.setattr("stars_ai.windows_autohost.time.sleep", sleep)

    _wait_for_human_orders(_config(human_turn_timeout_seconds="forever"), game, "GAME")

    output = capsys.readouterr().out
    assert "waiting forever" in output
    assert "is forever" in output
    assert "Generating the turn" in output
    assert clock.now - clock.start >= 100_000


def test_submitted_turn_still_times_out_while_stars_stays_open(tmp_path: Path, monkeypatch, capsys):
    game = _stage_game(tmp_path)
    _write_orders(game / "GAME.x1", submitted=True)
    _install_clock(monkeypatch)
    monkeypatch.setattr("stars_ai.windows_autohost._stars_client_running", lambda _exe: True)

    with pytest.raises(HumanTurnTimeoutError, match="The turn was not hosted"):
        _wait_for_human_orders(_config(human_turn_timeout_seconds=2), game, "GAME")

    output = capsys.readouterr().out
    assert "Close Stars!" in output
    assert "Generating the turn" not in output


def test_timeout_is_raised_before_the_host_launch():
    source = inspect.getsource(run_50_turn_game)
    assert source.index("_wait_for_human_orders") < source.index("_run_host_serialized")
    assert issubclass(HumanTurnTimeoutError, RuntimeError)
