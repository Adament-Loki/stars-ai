from __future__ import annotations

"""Decoder for Stars! type-45 current-turn player score records.

The record is 24 bytes.  The fields below are verified against the archived
turn-50 game: rank, score, owned-planet count, and six-field technology total
match the omniscient observer's independently decoded state.  Other payload
words are preserved as raw values until they have an equally strong label.
"""

from dataclasses import asdict, dataclass


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 2], "little")


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "little")


@dataclass(frozen=True)
class PlayerScoreRecord:
    player_id: int
    rank: int
    score: int
    planets: int
    tech_total: int
    raw_flags: int
    raw_u32_08: int
    raw_u16_14: int
    raw_u16_16: int
    raw_u16_18: int
    raw_u16_20: int

    def to_dict(self) -> dict:
        return asdict(self)


def parse_player_score(data: bytes) -> PlayerScoreRecord:
    """Decode one type-45 score record without guessing unverified metrics."""
    if len(data) != 24:
        raise ValueError(f"Player score record must be 24 bytes, got {len(data)}")
    flags = _u16(data, 0)
    # The low five bits are the zero-based player seat; upper bits are score
    # record flags and are retained verbatim.
    player_id = (flags & 0x1F) + 1
    return PlayerScoreRecord(
        player_id=player_id,
        rank=_u16(data, 2),
        score=_u32(data, 4),
        planets=_u16(data, 12),
        tech_total=_u16(data, 22),
        raw_flags=flags,
        raw_u32_08=_u32(data, 8),
        raw_u16_14=_u16(data, 14),
        raw_u16_16=_u16(data, 16),
        raw_u16_18=_u16(data, 18),
        raw_u16_20=_u16(data, 20),
    )
