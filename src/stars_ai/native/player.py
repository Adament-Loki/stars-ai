from __future__ import annotations
from dataclasses import dataclass, field
from .race import RaceData, parse_full_race_data

@dataclass
class PlayerRecord:
    player_number: int
    ship_design_count: int
    planets: int
    fleets: int
    starbase_design_count: int
    logo: int
    full_data_flag: bool
    byte7: int
    race: RaceData | None = None
    player_relations: list[int] = field(default_factory=list)
    name_singular: str | None = None
    name_plural: str | None = None
    raw_hex: str = ''


def _decode_stars_string(data: bytes) -> tuple[str, int]:
    """Port of the common Stars! nibble string representation used by StarsAPI Util.

    Returns decoded text and number of bytes consumed. This is intentionally conservative;
    malformed strings fall back to a printable placeholder rather than guessing.
    """
    if not data:
        return '', 0
    n = data[0]
    raw = data[1:1+n]
    tables = [
        ' aehilnorst', 'ABCDEFGHIJKLMNOP', 'QRSTUVWXYZ012345',
        '6789bcdfgjkmpquv', "wxyz+-,!.?:;'*%$"
    ]
    nibs=[]
    for b in raw:
        nibs.extend([b>>4, b&15])
    out=[]; i=0
    try:
        while i < len(nibs):
            x=nibs[i]; i+=1
            if x <= 10:
                out.append(tables[0][x])
            elif x in (11,12,13,14):
                if i>=len(nibs): break
                y=nibs[i]; i+=1
                out.append(tables[x-10][y])
            elif x == 15:
                if i+1>=len(nibs): break
                hi=nibs[i]; lo=nibs[i+1]; i+=2
                # Stars! escape stores swapped ASCII nibbles.
                out.append(chr((lo<<4)|hi))
    except Exception:
        return f'<encoded:{raw.hex()}>', 1+n
    return ''.join(out).rstrip('\x00'), 1+n


def parse_player(data: bytes) -> PlayerRecord:
    if len(data) < 8:
        raise ValueError('PLAYER block too short')
    player_number = data[0] + 1
    planets = data[2] | ((data[3] & 0x03) << 8)
    fleets = data[4] | ((data[5] & 0x03) << 8)
    full_data_flag = bool(data[6] & 0x04)
    idx=8; race=None; relations=[]
    if full_data_flag:
        if len(data) < 0x71:
            raise ValueError('Full PLAYER block too short')
        full = data[8:8+0x68]
        race = parse_full_race_data(full)
        idx=0x70
        rel_len=data[idx]; idx+=1
        relations=list(data[idx:idx+rel_len]); idx += rel_len
    singular=None; plural=None
    if idx < len(data):
        singular, used = _decode_stars_string(data[idx:]); idx += used
    if idx < len(data):
        plural, used = _decode_stars_string(data[idx:]); idx += used
        if used == 1 and idx < len(data): idx += 1
    return PlayerRecord(
        player_number=player_number,
        ship_design_count=data[1], planets=planets, fleets=fleets,
        starbase_design_count=(data[5] & 0xF0) >> 4,
        logo=data[6] >> 3, full_data_flag=full_data_flag, byte7=data[7],
        race=race, player_relations=relations,
        name_singular=singular, name_plural=plural, raw_hex=data.hex(' ')
    )
