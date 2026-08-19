from __future__ import annotations
from dataclasses import dataclass, field
from .common import u16,u32,read_n,content_len

@dataclass
class FleetRecord:
    fleet_id:int; owner:int; kind:int; position_object_id:int; x:int; y:int
    ship_types_mask:int; ship_count:list[int]=field(default_factory=lambda:[0]*16)
    ironium:int=0; boranium:int=0; germanium:int=0; population:int=0; fuel:int=0
    damaged_ship_types:int=0; damaged_ship_info:list[int]=field(default_factory=lambda:[0]*16)
    battle_plan:int|None=None; waypoint_count:int|None=None
    delta_x:int|None=None; delta_y:int|None=None; warp:int|None=None; unknown_bits_with_warp:int|None=None; mass:int|None=None
    raw_hex:str=''


def parse_fleet(data:bytes)->FleetRecord:
    fleet=data[0]|((data[1]&1)<<8); owner=(data[1]>>1)+1; kind=data[4]; two=(data[5]&8)==0
    f=FleetRecord(fleet,owner,kind,u16(data,6),u16(data,8),u16(data,10),u16(data,12),raw_hex=data.hex(' '))
    idx=14
    for bit in range(16):
        if f.ship_types_mask&(1<<bit):
            f.ship_count[bit]=u16(data,idx) if two else data[idx]; idx += 2 if two else 1
    if kind in (4,7):
        lens=u16(data,idx); idx+=2; vals=[]
        for code in [lens&3,(lens>>2)&3,(lens>>4)&3,(lens>>6)&3,(lens>>8)&3]:
            n=content_len(code); vals.append(read_n(data,idx,n)); idx+=n
        f.ironium,f.boranium,f.germanium,f.population,f.fuel=vals
    if kind==7:
        f.damaged_ship_types=u16(data,idx); idx+=2
        for bit in range(16):
            if f.damaged_ship_types&(1<<bit): f.damaged_ship_info[bit]=u16(data,idx); idx+=2
        f.battle_plan=data[idx]; f.waypoint_count=data[idx+1]; idx+=2
    else:
        f.delta_x=data[idx]; f.delta_y=data[idx+1]; f.warp=data[idx+2]&15; f.unknown_bits_with_warp=data[idx+2]&0xF0; idx+=4
        f.mass=u32(data,idx); idx+=4
    return f
