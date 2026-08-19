from __future__ import annotations
from dataclasses import dataclass
from .common import u16

STANDARD_ITEM_NAMES={0:'Mines (Auto Build)',1:'Factories (Auto Build)',2:'Defenses (Auto Build)',3:'Alchemy (Auto Build)',4:'Min Terraform (Auto Build)',5:'Max Terraform (Auto Build)',6:'Mineral Packets (Auto Build)',7:'Factory',8:'Mine',9:'Defenses',11:'Mineral Alchemy',12:'Unknown completion item',14:'Ironium Mineral Packet',15:'Boranium Mineral Packet',16:'Germanium Mineral Packet',17:'Mixed Mineral Packet',27:'Planetary Scanner'}

@dataclass
class QueueItem:
    item_id:int; count:int; complete_percent:int; item_type:int; item_name:str


def parse_queue(data:bytes, start:int=0)->list[QueueItem]:
    out=[]
    for i in range(start,len(data)-3,4):
        c1=u16(data,i); c2=u16(data,i+2); item_id=c1>>10; typ=c2&0x0F
        name=(STANDARD_ITEM_NAMES.get(item_id,f'StandardItem#{item_id}') if typ==2 else f'DesignSlot#{item_id}' if typ==4 else f'Item#{item_id}/Type#{typ}')
        out.append(QueueItem(item_id,c1&0x3FF,c2>>4,typ,name))
    return out
