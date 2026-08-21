from __future__ import annotations
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from stars_ai.adapters.stars_native import read_blocks, parse_xy, header_dict
from .player import PlayerRecord, parse_player
from .planet import PlanetRecord, parse_planet
from .fleet import FleetRecord, parse_fleet
from .waypoint import WaypointRecord, parse_waypoint
from .design import DesignRecord, parse_design
from .production import QueueItem, parse_queue
from .battle_plan import BattlePlanRecord, parse_battle_plan
from .objects import MapObjectRecord, parse_object
from .orders import ProductionQueueChange, PlanetChange, ResearchChange, parse_production_change, parse_planet_change, parse_research_change
from .player_scores import PlayerScoreRecord, parse_player_score

@dataclass
class PlayerState:
    header: dict[str,Any]
    game_name: str|None
    players:list[PlayerRecord]=field(default_factory=list)
    planets:list[PlanetRecord]=field(default_factory=list)
    fleets:list[FleetRecord]=field(default_factory=list)
    waypoints_by_fleet:dict[int,list[WaypointRecord]]=field(default_factory=dict)
    designs:list[DesignRecord]=field(default_factory=list)
    production_by_planet:dict[int,list[QueueItem]]=field(default_factory=dict)
    battle_plans:list[BattlePlanRecord]=field(default_factory=list)
    objects:list[MapObjectRecord]=field(default_factory=list)
    research_changes:list[ResearchChange]=field(default_factory=list)
    planet_changes:list[PlanetChange]=field(default_factory=list)
    production_changes:list[ProductionQueueChange]=field(default_factory=list)
    player_scores:list[PlayerScoreRecord]=field(default_factory=list)
    block_inventory:list[dict]=field(default_factory=list)
    planet_metadata:dict[int,dict]=field(default_factory=dict)

    @classmethod
    def from_files(cls, m_file:str|Path, xy_file:str|Path|None=None, x_file:str|Path|None=None)->'PlayerState':
        header, blocks, _ = read_blocks(m_file)
        # Stars! .m can concatenate turns; keep only blocks from the last FileHeader onward.
        last_start=0
        for i,b in enumerate(blocks):
            if b.type_id==8: last_start=i
        blocks=blocks[last_start:]
        xy=None; metadata={}; game_name=None
        if xy_file is not None:
            xy=parse_xy(xy_file); game_name=xy['game_name']
            metadata={p.planet_id:{'display_id':p.display_id,'name':p.name,'name_id':p.name_id,'x':p.x,'y':p.y} for p in xy['planets']}
        state=cls(header_dict(header),game_name,planet_metadata=metadata)
        pending_planet_for_queue=None
        waypoints=[]
        for b in blocks:
            state.block_inventory.append({'type_id':b.type_id,'name':b.name,'size':b.size})
            try:
                if b.type_id==6: state.players.append(parse_player(b.data))
                elif b.type_id in (13,14):
                    p=parse_planet(b.data,partial=(b.type_id==14)); state.planets.append(p); pending_planet_for_queue=p.planet_id
                elif b.type_id==28 and pending_planet_for_queue is not None: state.production_by_planet[pending_planet_for_queue]=parse_queue(b.data)
                elif b.type_id in (16,17): state.fleets.append(parse_fleet(b.data))
                # StarsAPI models WaypointTask (19) as a specialized
                # WaypointBlock. Both types occupy a fleet waypoint slot.
                elif b.type_id in (19,20): waypoints.append(parse_waypoint(b.data))
                elif b.type_id==26: state.designs.append(parse_design(b.data))
                elif b.type_id==30: state.battle_plans.append(parse_battle_plan(b.data))
                elif b.type_id==43: state.objects.append(parse_object(b.data))
                elif b.type_id==34: state.research_changes.append(parse_research_change(b.data))
                elif b.type_id==35: state.planet_changes.append(parse_planet_change(b.data))
                elif b.type_id==29: state.production_changes.append(parse_production_change(b.data))
                elif b.type_id==45: state.player_scores.append(parse_player_score(b.data))
            except Exception as exc:
                # Faithful reverse-engineering behavior: don't lose the file because one specialized decoder is incomplete.
                state.block_inventory[-1]['decode_error']=f'{type(exc).__name__}: {exc}'
        wi=0
        for f in state.fleets:
            n=f.waypoint_count or 0
            if n:
                state.waypoints_by_fleet[f.fleet_id]=waypoints[wi:wi+n]; wi+=n
        if x_file is not None and Path(x_file).exists():
            _, xblocks, _ = read_blocks(x_file)
            for b in xblocks:
                try:
                    if b.type_id==34: state.research_changes.append(parse_research_change(b.data))
                    elif b.type_id==35: state.planet_changes.append(parse_planet_change(b.data))
                    elif b.type_id==29: state.production_changes.append(parse_production_change(b.data))
                except Exception: pass
        return state


    def designs_for_player(self, player_id:int)->list[DesignRecord]:
        """Assign design records using the Stars! ordering used by M files: player ship counts in PLAYER order, then starbase counts."""
        ships=[d for d in self.designs if not d.is_starbase]
        bases=[d for d in self.designs if d.is_starbase]
        ship_off=0; base_off=0
        for player in self.players:
            nship=player.ship_design_count; nbase=player.starbase_design_count
            if player.player_number==player_id:
                return ships[ship_off:ship_off+nship] + bases[base_off:base_off+nbase]
            ship_off += nship; base_off += nbase
        return []

    def best_planets(self)->dict[int,PlanetRecord]:
        """
        Collapse duplicate/partial observations.

        Annual production decisions must prefer the freshest observation first,
        then the richest exact record. This prevents an older richer block from
        winning over current-year planet state.
        """
        out={}
        def score(p):
            observed_turn=int(p.observed_turn) if p.observed_turn is not None else -1
            richness=(
                (100 if p.has_installations else 0)
                +(50 if p.has_surface_minerals else 0)
                +(20 if p.can_see_environment() else 0)
                +(10 if p.owner is not None else 0)
            )
            return (observed_turn,richness)
        for p in self.planets:
            if p.planet_id not in out or score(p)>=score(out[p.planet_id]):
                out[p.planet_id]=p
        return out

    def best_fleets(self)->dict[tuple[int,int],FleetRecord]:
        """Collapse duplicate fleet observations, preferring full fleet records over partial sightings."""
        out={}
        for f in self.fleets:
            key=(f.owner,f.fleet_id)
            if key not in out or f.kind>out[key].kind: out[key]=f
        return out

    def to_dict(self)->dict[str,Any]:
        d=asdict(self)
        # enrich planets with .xy names/coords without changing raw parsed structure
        for p in d['planets']:
            meta=self.planet_metadata.get(p['planet_id'])
            if meta: p['map']=meta
        return d

    def summary(self)->dict[str,Any]:
        owner=self.players[0] if self.players else None
        return {
            'game_name':self.game_name,'year':self.header.get('year'),'player':self.header.get('player_number'),
            'race': owner.race.prt_name if owner and owner.race else None,
            'tech': asdict(owner.race.tech) if owner and owner.race else None,
            'counts': {'players':len(self.players),'planets':len(self.planets),'fleets':len(self.fleets),'designs':len(self.designs),'battle_plans':len(self.battle_plans),'objects':len(self.objects)},
            'object_kinds': sorted({o.object_kind for o in self.objects}),
            'block_types': sorted({x['name'] for x in self.block_inventory}),
        }
