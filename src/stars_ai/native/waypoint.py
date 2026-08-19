from __future__ import annotations
from dataclasses import dataclass
from .common import u16

@dataclass
class WaypointRecord:
    x:int; y:int; position_object:int; warp:int; waypoint_task:int; position_object_type:int; additional_bytes_hex:str=''


def parse_waypoint(data:bytes)->WaypointRecord:
    return WaypointRecord(u16(data,0),u16(data,2),u16(data,4),data[6]>>4,data[6]&0x0F,data[7],data[8:].hex(' '))
