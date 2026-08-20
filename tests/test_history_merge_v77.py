import hashlib
import os
from pathlib import Path

import pytest

from stars_ai.adapters.stars_native import NativeBlock, parse_planet_block, read_blocks
from stars_ai.native.history_merge import (
    HistoryMergeError,
    inspect_history_coverage,
    merge_history_file,
)
from stars_ai.native.x_writer import _encode_blocks
from stars_ai.windows_autohost import _history_sync_report


def _header(*, turn: int, player: int = 1, file_type: int) -> NativeBlock:
    data = bytearray(16)
    data[:4] = b"J3J3"
    data[4:8] = (0x12345678).to_bytes(4, "little")
    data[8:10] = (0x2A60).to_bytes(2, "little")
    data[10:12] = turn.to_bytes(2, "little")
    data[12:14] = ((321 << 5) | (player - 1)).to_bytes(2, "little")
    data[14] = file_type
    return NativeBlock(8, 16, bytes(data))


def _planet(
    planet_id: int,
    *,
    owner: int | None = None,
    environment: tuple[int, int, int] | None = (40, 50, 60),
    turn: int | None = None,
) -> NativeBlock:
    owner_bits = 31 if owner is None else owner - 1
    data = bytearray(
        (
            planet_id & 0xFF,
            ((planet_id >> 8) & 7) | (owner_bits << 3),
        )
    )
    flags = 0x0101 | (0x0002 if environment is not None else 0)
    data += flags.to_bytes(2, "little")
    if environment is not None:
        data += bytes((0, 70, 60, 50, *environment))
        if owner is not None:
            data += b"\0\0"
    if turn is not None:
        data += turn.to_bytes(2, "little")
    return NativeBlock(14, len(data), bytes(data))


def _write(path: Path, header: NativeBlock, blocks: list[NativeBlock]) -> None:
    path.write_bytes(_encode_blocks(header, [*blocks, NativeBlock(0, 0, b"")]))


def _write_h(path: Path, planets: list[NativeBlock], *, player: int = 1) -> None:
    counters = NativeBlock(32, 4, len(planets).to_bytes(2, "little") + b"\0\0")
    _write(path, _header(turn=0, player=player, file_type=4), [counters, *planets])


def _enemy_player(*, player: int, ships: int, starbases: int) -> NativeBlock:
    data = bytearray(8)
    data[0] = player - 1
    data[1] = ships
    data[5] = starbases << 4
    data[6] = (3 << 3) | 3
    data[7] = 1
    # Two non-empty encoded names; their exact text is irrelevant to the merge.
    data += b"\x01\x11\x01\x11"
    return NativeBlock(6, len(data), bytes(data))


def _partial_design(*, number: int, starbase: bool = False) -> NativeBlock:
    data = bytes(
        (
            3,
            1 | (number << 2) | (0x40 if starbase else 0),
            32 if starbase else 4,
            1,
            0 if starbase else 20,
            0,
            0,
        )
    )
    return NativeBlock(26, len(data), data)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_native_merge_adds_discovery_updates_turns_and_never_changes_m(tmp_path):
    h_path = tmp_path / "GAME.h1"
    m_path = tmp_path / "GAME.m1"
    _write_h(h_path, [_planet(2, turn=0), _planet(9, turn=0)])
    _write(
        m_path,
        _header(turn=1, file_type=3),
        [_planet(2), _planet(9), _planet(32)],
    )
    m_before = _digest(m_path)

    assert inspect_history_coverage(h_path, m_path)["ready"] is False
    result = merge_history_file(
        h_path,
        m_path,
        backup_path=tmp_path / "audit" / "pre.h1",
        merged_copy_path=tmp_path / "audit" / "post.h1",
    )

    assert result["status"] == "MERGED"
    assert result["new_planet_ids"] == [32]
    assert result["m_unchanged"] is True
    assert _digest(m_path) == m_before
    assert inspect_history_coverage(h_path, m_path)["ready"] is True
    _, blocks, _ = read_blocks(h_path)
    assert int.from_bytes(blocks[1].data[:2], "little") == 3
    planets = {
        parse_planet_block(block)["planet_id"]: block
        for block in blocks
        if block.type_id == 14
    }
    assert sorted(planets) == [2, 9, 32]
    assert all(block.data[-2:] == b"\1\0" for block in planets.values())

    first_h = _digest(h_path)
    second = merge_history_file(h_path, m_path)
    assert second["status"] == "VALIDATED_IDEMPOTENT"
    assert _digest(h_path) == first_h


def test_new_owner_state_keeps_older_environment_without_stale_turn(tmp_path):
    h_path = tmp_path / "GAME.h1"
    m_path = tmp_path / "GAME.m1"
    _write_h(h_path, [_planet(7, environment=(11, 22, 33), turn=2)])
    _write(
        m_path,
        _header(turn=3, file_type=3),
        [_planet(7, owner=2, environment=None)],
    )

    merge_history_file(h_path, m_path)
    _, blocks, _ = read_blocks(h_path)
    planet = next(block for block in blocks if block.type_id == 14)
    decoded = parse_planet_block(planet)
    assert decoded["owner"] == 2
    assert decoded["environment"] == {
        "gravity": 11,
        "temperature": 22,
        "radiation": 33,
    }
    assert planet.data[-2:] == b"\3\0"
    assert inspect_history_coverage(h_path, m_path)["ready"] is True


def test_mismatched_player_fails_without_touching_live_files(tmp_path):
    h_path = tmp_path / "GAME.h1"
    m_path = tmp_path / "GAME.m2"
    _write_h(h_path, [_planet(2, turn=0)], player=1)
    _write(m_path, _header(turn=1, player=2, file_type=3), [_planet(2)])
    h_before, m_before = _digest(h_path), _digest(m_path)

    with pytest.raises(HistoryMergeError, match="Player mismatch"):
        merge_history_file(h_path, m_path)

    assert _digest(h_path) == h_before
    assert _digest(m_path) == m_before


def test_post_install_validation_failure_atomically_restores_original_h(
    tmp_path, monkeypatch
):
    h_path = tmp_path / "GAME.h1"
    m_path = tmp_path / "GAME.m1"
    _write_h(h_path, [_planet(2, turn=0)])
    _write(m_path, _header(turn=1, file_type=3), [_planet(2), _planet(8)])
    h_before, m_before = h_path.read_bytes(), _digest(m_path)

    def fail_validation(*_args, **_kwargs):
        raise RuntimeError("injected post-install validation failure")

    monkeypatch.setattr(
        "stars_ai.native.history_merge.inspect_history_coverage", fail_validation
    )
    with pytest.raises(RuntimeError, match="injected post-install"):
        merge_history_file(
            h_path,
            m_path,
            backup_path=tmp_path / "pre.h1",
        )

    assert h_path.read_bytes() == h_before
    assert _digest(m_path) == m_before


def test_enemy_player_and_design_history_is_preserved_and_recounted(tmp_path):
    h_path = tmp_path / "GAME.h1"
    m_path = tmp_path / "GAME.m1"
    counters = NativeBlock(32, 4, b"\1\0\0\0")
    _write(
        h_path,
        _header(turn=0, file_type=4),
        [
            counters,
            _planet(4, owner=2, turn=0),
            _enemy_player(player=2, ships=1, starbases=1),
            _partial_design(number=3),
            _partial_design(number=2, starbase=True),
        ],
    )
    _write(m_path, _header(turn=1, file_type=3), [_planet(4, owner=2)])

    result = merge_history_file(h_path, m_path)
    assert result["enemy_players"] == 1
    assert result["enemy_designs"] == 2
    _, blocks, _ = read_blocks(h_path)
    player = next(block for block in blocks if block.type_id == 6)
    assert player.data[0] == 1
    assert player.data[1] == 1
    assert player.data[2:4] == b"\1\0"
    assert player.data[5] >> 4 == 1
    designs = [block for block in blocks if block.type_id == 26]
    assert len(designs) == 2
    assert not (designs[0].data[1] & 0x40)
    assert designs[1].data[1] & 0x40


def test_history_status_uses_embedded_observation_turns_not_timestamps(tmp_path):
    h_path = tmp_path / "GAME.h1"
    m_path = tmp_path / "GAME.m1"
    _write_h(h_path, [_planet(2, turn=0)])
    _write(m_path, _header(turn=1, file_type=3), [_planet(2), _planet(8)])

    # A newer H timestamp cannot make missing/stale semantic history look ready.
    newer = m_path.stat().st_mtime_ns + 5_000_000_000
    os.utime(h_path, ns=(newer, newer))
    report = _history_sync_report(tmp_path, "GAME", [1])
    assert report["ready"] is False
    assert report["players"][0]["coverage"]["missing_planet_ids"] == [8]

    merge_history_file(h_path, m_path)
    older = m_path.stat().st_mtime_ns - 5_000_000_000
    os.utime(h_path, ns=(older, older))
    assert _history_sync_report(tmp_path, "GAME", [1])["ready"] is True
