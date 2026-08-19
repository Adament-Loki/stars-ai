from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .adapters.stars_native import read_blocks
from .standard_mod import TechLevels


@dataclass(frozen=True)
class PlayerRaceTech:
    tech: TechLevels
    prt_id: int
    lrt_mask: int
    mt_mask: int


def parse_player_block_race_tech(data: bytes) -> PlayerRaceTech:
    """Decode race/technology state from a decrypted Stars! PLAYER block.

    StarsAPI PlayerBlock stores an 8-byte PLAYER preamble before the race
    ``fullData`` structure. Within fullData the six tech levels are at offsets
    18..23 in this order: Energy, Weapons, Propulsion, Construction,
    Electronics, Biotechnology.

    The same structure exposes the primary racial trait and masks used for
    lesser racial traits / mystery-trader item state. We expose those raw masks
    so availability filters can grow without changing this parser.
    """
    if len(data) < 84:
        raise ValueError(f"PLAYER block too short: {len(data)} bytes")
    base = 8
    levels = tuple(data[base + 18 : base + 24])
    if len(levels) != 6:
        raise ValueError("PLAYER block did not contain six technology levels")
    tech = TechLevels(*levels)
    return PlayerRaceTech(
        tech=tech,
        prt_id=data[base + 68],
        lrt_mask=int.from_bytes(data[base + 70 : base + 72], "little"),
        mt_mask=(data[base + 74] << 8) | data[base + 75],
    )


def player_race_tech_from_file(path: str | Path) -> PlayerRaceTech:
    """Read a player's .m# file and return live race/technology state."""
    _, blocks, _ = read_blocks(Path(path))
    player_blocks = [b.data for b in blocks if b.type_id == 6]
    if not player_blocks:
        raise ValueError(f"No PLAYER block found in {path}")
    # The first PLAYER block in a player's own .m# is the owning player record.
    return parse_player_block_race_tech(player_blocks[0])
