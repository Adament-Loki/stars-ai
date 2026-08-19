from __future__ import annotations
from dataclasses import dataclass, field
from .common import u16,u32
from .player import _decode_stars_string

HULL_NAMES = [
'Small Freighter','Medium Freighter','Large Freighter','Super Freighter','Scout','Frigate','Destroyer','Cruiser',
'Battle Cruiser','Battleship','Dreadnought','Privateer','Rogue','Galleon','Mini-Colony Ship','Colony Ship',
'Mini Bomber','B-17 Bomber','Stealth Bomber','B-52 Bomber','Midget Miner','Mini-Miner','Miner','Maxi-Miner',
'Ultra-Miner','Fuel Transport','Super Fuel Xport','Mini Mine Layer','Super Mine Layer','Nubian','Mini Morph','Meta Morph',
'Orbital Fort','Space Dock','Space Station','Ultra Station','Death Star'
]

@dataclass
class DesignSlot:
    category:int; item_id:int; count:int

@dataclass
class DesignRecord:
    is_full_design:bool; is_transferred:bool; is_starbase:bool; design_number:int; hull_id:int; hull_name:str
    pic:int; armor:int|None=None; slot_count:int|None=None; turn_designed:int|None=None
    total_built:int|None=None; total_remaining:int|None=None; slots:list[DesignSlot]=field(default_factory=list)
    partial_mass:int|None=None; name:str=''; raw_hex:str=''


def parse_design(data:bytes)->DesignRecord:
    full=bool(data[0]&4); transferred=bool(data[1]&0x80); starbase=bool(data[1]&0x40); num=(data[1]&0x3C)>>2
    hull=data[2]; idx=17 if full else 6
    d=DesignRecord(full,transferred,starbase,num,hull,HULL_NAMES[hull] if hull<len(HULL_NAMES) else f'Hull#{hull}',data[3],raw_hex=data.hex(' '))
    if full:
        d.armor=u16(data,4); d.slot_count=data[6]; d.turn_designed=u16(data,7); d.total_built=u32(data,9); d.total_remaining=u32(data,13)
        d.slots=[]
        idx=17
        for _ in range(d.slot_count):
            d.slots.append(DesignSlot(u16(data,idx),data[idx+2],data[idx+3])); idx+=4
    else:
        d.partial_mass=u16(data,4)
    d.name,_=_decode_stars_string(data[idx:])
    return d
