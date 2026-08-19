
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any

@dataclass
class PlayerSummary:
    player_id: int
    owned_planets: int = 0
    population: int = 0
    factories: int = 0
    mines: int = 0
    defenses: int = 0
    fleets: int = 0
    visible_ship_count: int = 0
    visible_fleet_mass: int = 0
    tech_sum: int = 0

@dataclass
class PlayerDelta:
    player_id: int
    planets_delta: int = 0
    population_delta: int = 0
    factories_delta: int = 0
    fleets_delta: int = 0
    ship_count_delta: int = 0
    tech_sum_delta: int = 0

@dataclass
class GameRecap:
    turn: int
    player_summaries: list[PlayerSummary]
    player_deltas: list[PlayerDelta]

    def to_dict(self):
        return asdict(self)

def _owner(obj):
    return getattr(obj, "owner_id", getattr(obj, "owner", None))

def _ship_count(f):
    v = getattr(f, "ship_count", getattr(f, "ship_counts", []))
    if isinstance(v, dict):
        return sum(int(x or 0) for x in v.values())
    if isinstance(v, (list, tuple)):
        return sum(int(x or 0) for x in v)
    return int(v or 0)

def _player_ids(state):
    ids = set()
    for p in getattr(state, "players", []) or []:
        pid = getattr(p, "player_id", getattr(p, "player_number", None))
        if pid is not None:
            ids.add(int(pid))
    for coll in ("planets", "fleets"):
        for obj in getattr(state, coll, []) or []:
            o = _owner(obj)
            if o is not None and o >= 0:
                ids.add(int(o))
    return ids

def summarize_player(state, pid):
    planets = [p for p in getattr(state, "planets", []) or [] if _owner(p) == pid]
    fleets = [f for f in getattr(state, "fleets", []) or [] if _owner(f) == pid]
    pobj = None
    for p in getattr(state, "players", []) or []:
        if getattr(p, "player_id", getattr(p, "player_number", None)) == pid:
            pobj = p
            break
    tech_sum = 0
    if pobj is not None:
        tech = getattr(pobj, "tech", None)
        if isinstance(tech, dict):
            tech_sum = sum(int(v or 0) for v in tech.values())
        else:
            for name in ("energy", "weapons", "propulsion", "construction", "electronics", "biotech"):
                tech_sum += int(getattr(pobj, name, 0) or 0)
    return PlayerSummary(
        player_id=pid,
        owned_planets=len(planets),
        population=sum(int(getattr(p, "population", 0) or 0) for p in planets),
        factories=sum(int(getattr(p, "factories", 0) or 0) for p in planets),
        mines=sum(int(getattr(p, "mines", 0) or 0) for p in planets),
        defenses=sum(int(getattr(p, "defenses", 0) or 0) for p in planets),
        fleets=len(fleets),
        visible_ship_count=sum(_ship_count(f) for f in fleets),
        visible_fleet_mass=sum(int(getattr(f, "mass", 0) or 0) for f in fleets),
        tech_sum=tech_sum,
    )

def compare(old, new):
    if old is None:
        return PlayerDelta(player_id=new.player_id)
    return PlayerDelta(
        player_id=new.player_id,
        planets_delta=new.owned_planets-old.owned_planets,
        population_delta=new.population-old.population,
        factories_delta=new.factories-old.factories,
        fleets_delta=new.fleets-old.fleets,
        ship_count_delta=new.visible_ship_count-old.visible_ship_count,
        tech_sum_delta=new.tech_sum-old.tech_sum,
    )

class GameObserver:
    def recap(self, current_state, *, previous_state=None, turn=0):
        summaries = [summarize_player(current_state, pid) for pid in sorted(_player_ids(current_state))]
        prev_map = {}
        if previous_state is not None:
            prev_map = {pid: summarize_player(previous_state, pid) for pid in _player_ids(previous_state)}
        deltas = [compare(prev_map.get(s.player_id), s) for s in summaries]
        return GameRecap(turn=turn, player_summaries=summaries, player_deltas=deltas)
