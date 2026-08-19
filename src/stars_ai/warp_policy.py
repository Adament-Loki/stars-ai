
from __future__ import annotations
from .util import distance
from .fuel_planner import fastest_fuel_safe_warp, scout_one_way_warp

def mission_warp(fleet,target_position,mission:str,pressure:float=1.0)->int:
    d=distance(fleet.position,target_position); n=getattr(fleet,'native',{}) or {}; fp=n.get('fuel_profile'); flags=n.get('race_fuel_flags',{})
    if fp and fp.get('groups'):
        m=str(mission or '').lower()
        if m in ('scan','recon'):
            safe=scout_one_way_warp(
                fp,d,
                ife=bool(flags.get('ife')),
                ce=bool(flags.get('ce')),
                pressure=pressure,
            )
        else:
            safe=fastest_fuel_safe_warp(fp,d,mission,ife=bool(flags.get('ife')),ce=bool(flags.get('ce')))
        return int(safe) if safe is not None else 1
    role=str(getattr(fleet,'role','unknown')); m=str(mission or '').lower()
    if m in ('colonize','reposition_for_colonize'): return 7 if d<80 else 8
    if role in ('miner','minelayer'): return 7 if d<40 else 8
    if role=='freighter': return 7
    return 8
