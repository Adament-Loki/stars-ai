from __future__ import annotations
from dataclasses import dataclass
from .common import u16,u32

MT_ITEM_NAMES={0:'Multi Cargo Pod',1:'Multi Function Pod',2:'Langston Shield',3:'Mega Poly Shell',4:'Alien Miner',5:'Hush-a-Boom',6:'Anti Matter Torpedo',7:'Multi Contained Munition',8:'Mini Morph',9:'Enigma Pulsar',10:'Genesis Device',11:'Jump Gate',12:'Ship'}

@dataclass
class MapObjectRecord:
    object_kind:str; number:int|None=None; owner:int|None=None; object_type:int|None=None; x:int|None=None; y:int|None=None
    fields:dict|None=None; raw_hex:str=''


def parse_object(data:bytes)->MapObjectRecord:
    if len(data)==2:
        return MapObjectRecord('Count',fields={'count':u16(data,0)},raw_hex=data.hex(' '))
    oid=u16(data,0); number=oid&0x01FF; owner=((oid&0x1E00)>>9)+1; typ=oid>>13; x=u16(data,2); y=u16(data,4)
    if typ==0:
        fields={'mine_count':u32(data,6),'minefield_type':data[12] if len(data)>12 else None,'detonating':(data[13]==1) if len(data)>13 else None,'visibility_mask':u16(data,14) if len(data)>=16 else None}; kind='Minefield'
    elif typ==1:
        fields={}; kind='PacketOrSalvage'
    elif typ==2:
        fields={'wormhole_id':u16(data,0)%4096,'been_through_bits':u16(data,8),'can_see_bits':u16(data,10),'target_id':u16(data,12)%4096}; kind='Wormhole'
    elif typ==3:
        bits=u16(data,14); names=[name for bit,name in MT_ITEM_NAMES.items() if bits&(1<<bit)]
        if bits==0: names=['Research']
        fields={'x_dest':u16(data,6),'y_dest':u16(data,8),'warp':data[10]&0xF,'met_bits':u16(data,12),'item_bits':bits,'items':names,'turn_no':u16(data,16)}; kind='MysteryTrader'
    else:
        fields={}; kind=f'UnknownType{typ}'
    return MapObjectRecord(kind,number,owner,typ,x,y,fields,data.hex(' '))
