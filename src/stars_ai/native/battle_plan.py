from __future__ import annotations
from dataclasses import dataclass
from .common import u16
from .player import _decode_stars_string

TACTICS={0:'Disengage',1:'Disengage if challenged',2:'Minimize damage to self',3:'Maximize net damage',4:'Maximize damage ratio',5:'Maximize damage'}
TARGETS={0:'None/Disengage',1:'Any',2:'Starbase',3:'Armed Ships',4:'Bombers/Freighters',5:'Unarmed Ships',6:'Fuel Transports',7:'Freighters'}
ATTACK_WHO={0:'Nobody',1:'Enemies',2:'Neutral & Enemies',3:'Everyone'}

@dataclass
class BattlePlanRecord:
    owner_player_id:int; plan_id:int; tactic:int; tactic_name:str; dump_cargo:bool
    primary_target:int; primary_target_name:str; secondary_target:int; secondary_target_name:str
    attack_who:int; attack_who_name:str; name:str|None; deleted:bool; raw_hex:str=''


def parse_battle_plan(data:bytes)->BattlePlanRecord:
    w0=u16(data,0); w1=u16(data,2); deleted=len(data)==4; attack=w1>>8
    name=None if deleted else _decode_stars_string(data[4:])[0]
    return BattlePlanRecord(w0&0xF,(w0>>4)&0xF,(w0>>8)&0xF,TACTICS.get((w0>>8)&0xF,'Unknown'),bool((w0>>15)&1),w1&0xF,TARGETS.get(w1&0xF,'Unknown'),(w1>>4)&0xF,TARGETS.get((w1>>4)&0xF,'Unknown'),attack,ATTACK_WHO.get(attack,f'Player#{attack-3}' if attack>=4 else 'Unknown'),name,deleted,data.hex(' '))
