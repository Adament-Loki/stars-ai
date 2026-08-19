from __future__ import annotations
from dataclasses import dataclass, field

PRT_NAMES = {
    0:'Hyper Expansion', 1:'Super Stealth', 2:'War Monger', 3:'Claim Adjuster',
    4:'Inner Strength', 5:'Space Demolition', 6:'Packet Physics', 7:'Interstellar Traveler',
    8:'Alternate Reality', 9:'Jack of All Trades'
}
LRT_NAMES = ['IFE','TT','ARM','ISB','GR','UR','MA','NRSE','CE','OBRM','NAS','LSP','BET','RS']
TECH_FIELDS = ('energy','weapons','propulsion','construction','electronics','biotechnology')

@dataclass(frozen=True)
class TechLevels:
    energy:int; weapons:int; propulsion:int; construction:int; electronics:int; biotechnology:int

@dataclass
class RaceData:
    tech: TechLevels
    prt_id: int
    prt_name: str
    lrt_mask: int
    lrts: list[str]
    mt_mask: int
    growth_raw: int
    population_efficiency_raw: int
    economy_raw: list[int]
    research_cost_raw: list[int]
    spend_leftover_points_on_raw: int
    flags_73: int
    hab_raw: bytes = b''
    hab_center: tuple[int,int,int] = (50,50,50)
    hab_low: tuple[int,int,int] = (15,15,15)
    hab_high: tuple[int,int,int] = (85,85,85)
    hab_immune: tuple[bool,bool,bool] = (False,False,False)
    universal_hab: bool = False
    full_data_hex: str = ''


def decode_lrt_mask(mask: int) -> list[str]:
    return [name for bit, name in enumerate(LRT_NAMES) if mask & (1 << bit)]


def parse_full_race_data(full: bytes) -> RaceData:
    if len(full) < 0x68:
        raise ValueError(f'Expected 0x68 bytes of Player fullData, got {len(full)}')
    tech = TechLevels(*tuple(full[18:24]))
    prt = full[68]
    # LRT is a little-endian 16-bit bitset in StarsAPI race-builder indexing.
    lrt_mask = int.from_bytes(full[70:72], 'little')
    # StarsAPI setMtMask writes high byte first: fullData[74]=mask>>8, [75]=mask&0xff.
    mt_mask = (full[74] << 8) | full[75]
    hab_raw=bytes(full[8:17])
    centers=tuple(int(x) for x in full[8:11])
    lows=tuple(int(x) for x in full[11:14])
    highs=tuple(int(x) for x in full[14:17])

    # StarsAPI PlayerBlock.makeBeefyFullData documents 0xFF across the
    # corresponding hab bytes as immunity. Per-axis immunity is therefore
    # recognized when center/low/high are all 0xFF.
    immune=tuple(
        centers[i]==0xFF and lows[i]==0xFF and highs[i]==0xFF
        for i in range(3)
    )
    universal=all(immune)

    return RaceData(
        tech=tech,
        prt_id=prt,
        prt_name=PRT_NAMES.get(prt, f'PRT#{prt}'),
        lrt_mask=lrt_mask,
        lrts=decode_lrt_mask(lrt_mask),
        mt_mask=mt_mask,
        growth_raw=full[17],
        population_efficiency_raw=full[54],
        economy_raw=list(full[55:61]),
        research_cost_raw=list(full[62:68]),
        spend_leftover_points_on_raw=full[61],
        flags_73=full[73],
        hab_raw=hab_raw,
        hab_center=centers,
        hab_low=lows,
        hab_high=highs,
        hab_immune=immune,
        universal_hab=universal,
        full_data_hex=full.hex(' '),
    )
