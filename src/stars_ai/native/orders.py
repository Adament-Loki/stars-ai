from __future__ import annotations
from dataclasses import dataclass
from .common import u16
from .production import QueueItem, parse_queue

@dataclass
class ProductionQueueChange:
    planet_id:int; items:list[QueueItem]

@dataclass
class PlanetChange:
    planet_id:int; leftover_only_raw:int|None; route_destination:int|None; raw_hex:str

@dataclass
class ResearchChange:
    percent:int|None; packed_field_flags:int|None; raw_hex:str


def parse_production_change(data:bytes)->ProductionQueueChange:
    return ProductionQueueChange(u16(data,0)&0x7FF, parse_queue(data,2))


def parse_planet_change(data:bytes)->PlanetChange:
    return PlanetChange(u16(data,0)&0x7FF, u16(data,2) if len(data)>=4 else None, u16(data,4)&0x7FF if len(data)>=6 else None, data.hex(' '))


def parse_research_change(data:bytes)->ResearchChange:
    return ResearchChange(data[0] if data else None, data[1] if len(data)>1 else None, data.hex(' '))
