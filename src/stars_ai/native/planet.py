from __future__ import annotations
from dataclasses import dataclass
from .common import u16, read_n, content_len

@dataclass
class PlanetRecord:
    planet_id:int; owner:int|None; is_homeworld:bool
    is_in_use_or_robber_baron:bool; has_environment_info:bool
    bit_off_for_remote_mining_and_robber_baron:bool; weird_bit:bool
    has_route:bool; has_surface_minerals:bool; has_artifact:bool
    has_installations:bool; is_terraformed:bool; has_starbase:bool
    ironium_conc:int|None=None; boranium_conc:int|None=None; germanium_conc:int|None=None
    gravity:int|None=None; temperature:int|None=None; radiation:int|None=None
    orig_gravity:int|None=None; orig_temperature:int|None=None; orig_radiation:int|None=None
    defenses_estimate:int|None=None; pop_estimate:int|None=None
    ironium:int=0; boranium:int=0; germanium:int=0; population:int=0
    excess_pop:int|None=None; mines:int|None=None; factories:int|None=None; defenses:int|None=None
    unknown_installations_byte:int|None=None
    contribute_only_leftover_resources_to_research:bool|None=None; has_scanner:bool|None=None
    starbase_design:int|None=None; starbase_raw:str|None=None; route_short:int|None=None
    observed_turn:int|None=None; raw_hex:str=''

    def can_see_environment(self)->bool:
        return self.has_environment_info or ((self.has_surface_minerals or self.is_in_use_or_robber_baron) and not self.bit_off_for_remote_mining_and_robber_baron)


def parse_planet(data:bytes, *, partial:bool)->PlanetRecord:
    planet = data[0] | ((data[1]&7)<<8)
    owner=(data[1]&0xF8)>>3; owner=None if owner==31 else owner+1
    flags=u16(data,2); idx=4
    p=PlanetRecord(
        planet_id=planet, owner=owner, is_homeworld=bool(flags&0x80),
        is_in_use_or_robber_baron=bool(flags&0x04), has_environment_info=bool(flags&0x02),
        bit_off_for_remote_mining_and_robber_baron=bool(flags&0x01), weird_bit=bool(flags&0x8000),
        has_route=bool(flags&0x4000), has_surface_minerals=bool(flags&0x2000), has_artifact=bool(flags&0x1000),
        has_installations=bool(flags&0x0800), is_terraformed=bool(flags&0x0400), has_starbase=bool(flags&0x0200),
        raw_hex=data.hex(' ')
    )
    if p.can_see_environment():
        pre=data[idx]; plen=1+((pre&0x30)>>4)+((pre&0x0C)>>2)+(pre&3); idx += plen
        p.ironium_conc=data[idx]; p.boranium_conc=data[idx+1]; p.germanium_conc=data[idx+2]; idx+=3
        p.gravity=data[idx]; p.temperature=data[idx+1]; p.radiation=data[idx+2]; idx+=3
        if p.is_terraformed:
            p.orig_gravity=data[idx]; p.orig_temperature=data[idx+1]; p.orig_radiation=data[idx+2]; idx+=3
        if p.owner is not None:
            est=u16(data,idx); idx+=2; p.defenses_estimate=est//4096; p.pop_estimate=est%4096
    if p.has_surface_minerals:
        lens=data[idx]; idx+=1; vals=[]
        for shift in (0,2,4,6):
            n=content_len((lens>>shift)&3); vals.append(read_n(data,idx,n)); idx+=n
        p.ironium,p.boranium,p.germanium,p.population=vals
    if p.has_installations:
        inst=data[idx:idx+8]; idx+=8
        p.excess_pop=inst[0]
        p.mines=inst[1] | ((inst[2]&0x0F)<<8)
        p.factories=((inst[2]&0xF0)>>4) | (inst[3]<<4)
        p.defenses=inst[4]
        p.unknown_installations_byte=inst[5]
        p.contribute_only_leftover_resources_to_research=bool(inst[6]&0x80)
        p.has_scanner=(inst[6]&1)==0
    if p.has_starbase:
        if partial:
            p.starbase_design=data[idx]&0x0F; idx+=1
        else:
            p.starbase_raw=data[idx:idx+4].hex(' '); p.starbase_design=data[idx]&0x0F; idx+=4
    if p.has_route and not partial:
        p.route_short=u16(data,idx); idx+=2
    if idx+2==len(data):
        p.observed_turn=u16(data,idx); idx+=2
    return p
