"""Phase 8 application lifecycle and durable-memory transaction coverage."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from stars_ai.adapters.stars_native import NativeBlock
from stars_ai.autohost.bridges import NativeOrderBridge
from stars_ai.autohost.lifecycle import HostedTurnLifecycle, TurnLifecyclePhase
from stars_ai.ai.state_store import AIStateStore
from stars_ai.memory import AgentMemory
from stars_ai.native.x_writer import FILEHASH_CANONICAL_TAIL, _encode_blocks
from stars_ai.windows_autohost import WindowsAutoHostConfig, run_50_turn_game


def _header(
    *, player: int = 1, file_type: int = 3, turn: int = 0, submitted: bool = False
) -> NativeBlock:
    data = bytearray(16)
    data[:4] = b"J3J3"
    data[4:8] = (0x12345678).to_bytes(4, "little")
    data[8:10] = (0x2A60).to_bytes(2, "little")
    data[10:12] = int(turn).to_bytes(2, "little")
    data[12:14] = ((321 << 5) | (int(player) - 1)).to_bytes(2, "little")
    data[14] = int(file_type)
    data[15] = 1 if submitted else 0
    return NativeBlock(8, 16, bytes(data))


def _write_native(path: Path, header: NativeBlock, blocks: list[NativeBlock] | None = None) -> None:
    path.write_bytes(_encode_blocks(header, list(blocks or []) + [NativeBlock(0, 0, b"")]))


def _seed(root: Path) -> Path:
    root.mkdir()
    _write_native(root / "GAME.hst", _header(file_type=2))
    _write_native(root / "GAME.xy", _header(file_type=0))
    for player_id in (1, 2):
        _write_native(root / f"GAME.m{player_id}", _header(player=player_id))
        _write_native(
            root / f"GAME.h{player_id}",
            _header(player=player_id, file_type=4),
            [NativeBlock(32, 4, b"\0\0\0\0")],
        )
        _write_native(
            root / f"GAME.x{player_id}",
            _header(player=player_id, file_type=1, submitted=True),
            [NativeBlock(9, 17, b"\0\0" + FILEHASH_CANONICAL_TAIL)],
        )
    return root


class _TransactionalCopyBridge(NativeOrderBridge):
    """A test bridge that exposes the same transaction hooks as the AI bridge."""

    def __init__(self, *, fail_player: int | None = None) -> None:
        self.fail_player = fail_player
        self.events: list[str] = []
        self.players: list[int] = []
        self.turn_tag: str | None = None

    def create_x_file(
        self,
        *,
        player_id: int,
        m_path: Path,
        xy_path: Path,
        existing_x_path: Path | None,
        output_x_path: Path,
        turn_dir: Path,
        client_x_path: Path | None = None,
    ) -> Path:
        del m_path, xy_path, client_x_path
        self.players.append(player_id)
        if player_id == self.fail_player:
            raise RuntimeError("simulated native writer failure")
        assert existing_x_path is not None
        shutil.copy2(existing_x_path, output_x_path)
        (turn_dir / f"{self.turn_tag}-player-{player_id:02d}-DECISION_REPORT.txt").write_text(
            f"decision report for player {player_id}\n", encoding="utf-8"
        )
        return output_x_path

    def commit_pending_memory(self) -> None:
        self.events.append("commit")

    def discard_pending_memory(self) -> None:
        self.events.append("rollback")


def _config(tmp_path: Path, seed: Path) -> WindowsAutoHostConfig:
    game_dir = tmp_path / "stars"
    game_dir.mkdir()
    executable = game_dir / "stars!.exe"
    executable.write_bytes(b"test host")
    return WindowsAutoHostConfig(
        stars_exe=str(executable),
        seed_dir=str(seed),
        output_dir=str(tmp_path / "output"),
        player_ids=[1, 2],
        turns=1,
        auto_merge_history=False,
        keep_every_turn=False,
        print_observer_each_turn=False,
    )


def _stub_host_lifecycle(monkeypatch, *, returncode: int) -> None:
    """Replace the Stars! process only; retain staging, archive, and bridge flow."""

    import stars_ai.windows_autohost as autohost

    years = iter((2400, 2401))
    monkeypatch.setattr(autohost, "_history_sync_barrier", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(autohost, "_history_sync_report", lambda *_args, **_kwargs: {"ready": True})
    monkeypatch.setattr(autohost, "_auto_merge_histories", lambda *_args, **_kwargs: {"ready": True})
    monkeypatch.setattr(autohost, "_pre_host_audit", lambda *_args, **_kwargs: {"ready": True, "players": []})
    monkeypatch.setattr(autohost, "_post_host_audit", lambda *_args, **_kwargs: {"all_consumed": True})
    monkeypatch.setattr(autohost, "_read_year", lambda *_args, **_kwargs: next(years))
    monkeypatch.setattr(
        autohost,
        "_run_host_serialized",
        lambda *_args, **_kwargs: (subprocess.CompletedProcess(["stars!"], returncode), True),
    )


def test_hosted_turn_lifecycle_commits_only_after_success():
    bridge = _TransactionalCopyBridge()
    lifecycle = HostedTurnLifecycle(bridge)

    lifecycle.activate("turn-001")
    lifecycle.begin_staging()
    lifecycle.record_order_file("GAME.x1")
    assert bridge.events == ["rollback"]

    assert lifecycle.settle_after_host(True) is TurnLifecyclePhase.COMMITTED
    assert bridge.events == ["rollback", "commit"]


def test_lifecycle_rolls_back_and_commits_actual_ai_state_store(tmp_path):
    store = AIStateStore.for_player(tmp_path, 1)
    AgentMemory(player_id=1, last_year=2400).save(store.committed_path)

    class _StoreBridge:
        def commit_pending_memory(self) -> None:
            store.commit()

        def discard_pending_memory(self) -> None:
            store.rollback()

    lifecycle = HostedTurnLifecycle(_StoreBridge())
    lifecycle.activate("turn-001")
    lifecycle.begin_staging()
    store.stage(AgentMemory(player_id=1, last_year=2401))
    lifecycle.settle_after_host(False)

    assert AgentMemory.load(store.committed_path).last_year == 2400
    assert not store.pending_path.exists()

    lifecycle.activate("turn-002")
    lifecycle.begin_staging()
    store.stage(AgentMemory(player_id=1, last_year=2402))
    lifecycle.settle_after_host(True)

    assert AgentMemory.load(store.committed_path).last_year == 2402
    assert not store.pending_path.exists()


def test_run_commits_after_success_and_archives_pre_host_reports(tmp_path, monkeypatch):
    seed = _seed(tmp_path / "seed")
    cfg = _config(tmp_path, seed)
    bridge = _TransactionalCopyBridge()
    _stub_host_lifecycle(monkeypatch, returncode=0)

    results = run_50_turn_game(cfg, bridge)

    assert results[0].success is True
    assert bridge.players == [1, 2]
    assert bridge.events == ["rollback", "commit"]
    archive = Path(cfg.output_dir) / "logs" / "turn-archive" / "turn-001"
    assert (archive / "00-pre-write" / "manifest.json").exists()
    assert (archive / "10-pre-host" / "manifest.json").exists()
    assert (archive / "20-post-host-attempt" / "manifest.json").exists()
    assert (archive / "30-committed" / "manifest.json").exists()
    assert any((archive / "10-pre-host" / "logs").glob("*-DECISION_REPORT.txt"))


def test_run_rolls_back_when_host_does_not_succeed(tmp_path, monkeypatch):
    seed = _seed(tmp_path / "seed")
    cfg = _config(tmp_path, seed)
    bridge = _TransactionalCopyBridge()
    _stub_host_lifecycle(monkeypatch, returncode=1)

    results = run_50_turn_game(cfg, bridge)

    assert results[0].success is False
    assert bridge.events == ["rollback", "rollback"]
    archive = Path(cfg.output_dir) / "logs" / "turn-archive" / "turn-001"
    assert (archive / "20-post-host-attempt" / "manifest.json").exists()
    assert not (archive / "30-committed").exists()


def test_run_rolls_back_when_native_write_fails(tmp_path, monkeypatch):
    seed = _seed(tmp_path / "seed")
    cfg = _config(tmp_path, seed)
    bridge = _TransactionalCopyBridge(fail_player=2)
    _stub_host_lifecycle(monkeypatch, returncode=0)

    results = run_50_turn_game(cfg, bridge)

    assert results[0].success is False
    assert "simulated native writer failure" in results[0].message
    assert bridge.events == ["rollback", "rollback"]
    archive = Path(cfg.output_dir) / "logs" / "turn-archive" / "turn-001"
    assert (archive / "99-failure" / "manifest.json").exists()
