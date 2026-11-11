from dataclasses import replace
from pathlib import Path

from stars_ai.adapters.stars_native import NativeBlock, parse_file_header
from stars_ai.native.x_writer import (
    _copy_client_fleet_split_pairs,
    _encode_blocks,
    _fleet_split_transfer_blocks as writer_blocks,
    _fresh_filehash_block,
)


def _header() -> NativeBlock:
    data = bytearray(16)
    data[4:8] = (775786734).to_bytes(4, "little")
    data[10:12] = (39).to_bytes(2, "little")
    data[12:14] = ((111 << 5) | 0).to_bytes(2, "little")
    data[14] = 1
    data[15] = 1
    return NativeBlock(8, 16, bytes(data))


def _captured_split_x(tmp_path: Path) -> tuple[Path, object]:
    """Create the exact Player-1 Type24 -> Type23 payload from turn 040."""
    header = _header()
    split = NativeBlock(24, 2, bytes.fromhex("06 00"))
    move_ships = NativeBlock(23, 9, bytes.fromhex("06 00 13 00 22 01 00 fd ff"))
    client_x = tmp_path / "captured.x1"
    client_x.write_bytes(_encode_blocks(
        header,
        [_fresh_filehash_block([split, move_ships]), split, move_ships, NativeBlock(0, 0, b"")],
    ))
    return client_x, parse_file_header(header.data)


def test_synthesized_split_matches_the_player_one_capture():
    """The rejected codec put the new fleet first and used a positive count."""
    class _State:
        player_id = 1

    split, move = writer_blocks(
        _State(),
        source_fleet_id=6,
        destination_fleet_id=19,
        design_slot=0,
        ships=3,
    )

    assert split.data.hex(" ") == "06 00"
    assert move.data.hex(" ") == "06 00 13 00 22 01 00 fd ff"


def test_singleton_split_uses_a_negative_one_and_the_owner_tag():
    class _State:
        player_id = 3

    split, move = writer_blocks(
        _State(),
        source_fleet_id=2,
        destination_fleet_id=24,
        design_slot=6,
        ships=1,
    )

    assert split.data.hex(" ") == "02 04"
    assert move.data.hex(" ") == "02 04 18 04 22 40 00 ff ff"


def test_player_one_client_split_capture_is_preserved_verbatim(tmp_path):
    """P1 split six identical scouts into two fleets of three in this capture."""
    client_x, m_header = _captured_split_x(tmp_path)
    copied, sources, diagnostics = _copy_client_fleet_split_pairs(
        client_x,
        m_header=m_header,
        player_id=1,
        owned_fleet_ids={6},
    )

    assert sources == [6]
    assert [(block.type_id, block.data.hex(" ")) for block in copied] == [
        (24, "06 00"),
        (23, "06 00 13 00 22 01 00 fd ff"),
    ]
    assert diagnostics == [{
        "status": "PRESERVED",
        "source_fleet_id": 6,
        "type24_hex": "06 00",
        "type23_hex": "06 00 13 00 22 01 00 fd ff",
        "path": str(client_x),
        "preservation_only": True,
    }]


def test_client_split_capture_is_rejected_for_a_different_turn(tmp_path):
    client_x, m_header = _captured_split_x(tmp_path)
    copied, sources, diagnostics = _copy_client_fleet_split_pairs(
        client_x,
        m_header=replace(m_header, turn=m_header.turn + 1),
        player_id=1,
        owned_fleet_ids={6},
    )

    assert copied == []
    assert sources == []
    assert diagnostics[0]["status"] == "REJECTED"
    assert "current game/turn/player" in diagnostics[0]["reason"]
