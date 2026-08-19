from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Iterable
import math

from .base import TurnAdapter
from ..models import GameState, Planet, Fleet, Position, Tech, RaceProfile, OrderSet
from ..native.player_state import PlayerState
from ..fuel_planner import design_fuel_profile, fleet_fuel_profile, has_ife, has_ce
from ..starbase_capabilities import starbase_capabilities


def _infer_xy(m_path: Path) -> Path | None:
    # Stars! convention: GAME.m1 -> GAME.xy
    name = m_path.name
    if '.m' in name.lower():
        stem = name[: name.lower().rfind('.m')]
        cand = m_path.with_name(stem + '.xy')
        if cand.exists():
            return cand
    cand = m_path.with_suffix('.xy')
    return cand if cand.exists() else None


def _pick_current_player(native: PlayerState, player_id: int):
    for p in native.players:
        if p.player_number == player_id and p.full_data_flag:
            return p
    for p in native.players:
        if p.player_number == player_id:
            return p
    return native.players[0] if native.players else None


def _role_for_design(d) -> str:
    armed=any(s.count>0 and s.category in (16,32,64) for s in d.slots)
    if d.hull_id in (14,15): return 'colony'
    if d.hull_id==4: return 'scout'
    if d.hull_id in (20,21,22,23,24): return 'miner'
    if d.hull_id in (27,28): return 'minelayer'
    if d.hull_id in (0,1,2,3,11,12,13,25,26): return 'freighter'
    if armed: return 'combat'
    return 'unknown'

def _design_roles(native: PlayerState, player_id: int) -> dict[int,str]:
    return {d.design_number:_role_for_design(d) for d in native.designs_for_player(player_id) if not d.is_starbase}

def _fleet_role(ship_count: list[int], roles: dict[int, str]) -> str:
    present = {roles.get(i, 'unknown') for i, count in enumerate(ship_count) if count}
    for preferred in ('colony', 'combat', 'miner', 'scout', 'freighter'):
        if preferred in present:
            return preferred
    return 'unknown'


def _environment_tuple(p):
    vals=(getattr(p,"gravity",None),getattr(p,"temperature",None),getattr(p,"radiation",None))
    if any(v is None for v in vals):
        return None
    return tuple(int(v) for v in vals)

def _estimate_habitability_from_homeworld(planet, home_env):
    """Deprecated compatibility helper retained for older tests/non-native callers."""
    env=_environment_tuple(planet)
    if env is None or home_env is None:
        return None
    delta=sum(abs(a-b) for a,b in zip(env,home_env))/3.0
    return max(-15,min(100,round(100.0-delta*2.2)))


def _habitability_from_race(planet,race_data):
    """
    Port of StarsAPI craigstars.Race.getPlanetHabitability.

    Race fullData bytes:
      8..10  centers
      11..13 lows
      14..16 highs
    An immune axis is encoded as FF/FF/FF.
    """
    env=_environment_tuple(planet)
    if env is None or race_data is None:
        return None
    if getattr(race_data,"universal_hab",False):
        return 100

    points=0.0
    red=0
    ideality=10000.0
    centers=race_data.hab_center
    lows=race_data.hab_low
    highs=race_data.hab_high
    immune=race_data.hab_immune

    for i,value in enumerate(env):
        if immune[i]:
            points += 10000.0
            continue

        center=int(centers[i]); low=int(lows[i]); high=int(highs[i])
        if low <= value <= high:
            if center > value:
                radius=max(1,center-low)
                tmp=center-value
            else:
                radius=max(1,high-center)
                tmp=value-center
            from_ideal=100.0-(abs(value-center)*100.0/radius)
            poor=(tmp*2)-radius
            points += max(0.0,from_ideal)**2
            if poor>0:
                ideality *= max(0.0,(radius*2-poor)/(radius*2))
        else:
            delta=(value-high) if value>high else (low-value)
            red += min(int(delta),15)

    if red:
        return -red
    value=int(math.sqrt(points/3.0)+0.9)
    return int(value*ideality/10000.0)


class NativeCoreTurnAdapter(TurnAdapter):
    """Bridge StarsAPI-inspired PlayerState into the AI's stable GameState model.

    Reading is native. Order writing remains JSON at this layer so the decision engine can
    be tested independently of the native .x# order writer.
    """

    def __init__(self, xy_path: str | Path | None = None, x_path: str | Path | None = None):
        self.xy_path = Path(xy_path) if xy_path else None
        self.x_path = Path(x_path) if x_path else None
        self.last_native_state: PlayerState | None = None

    def read_state(self, path: Path, player_id: int) -> GameState:
        m_path = Path(path)
        xy = self.xy_path or _infer_xy(m_path)
        native = PlayerState.from_files(m_path, xy, self.x_path)
        self.last_native_state = native
        player = _pick_current_player(native, player_id)
        if player is None:
            raise ValueError('No PLAYER block found in native Stars! turn file.')
        if player.player_number != player_id:
            raise ValueError(f'Native turn does not contain requested player {player_id}.')

        race_data = player.race
        if race_data:
            t = race_data.tech
            tech = Tech(t.energy, t.weapons, t.propulsion, t.construction, t.electronics, t.biotechnology)
            # Stars! stores the common 15% growth setting as raw 15.
            growth = race_data.growth_raw / 100.0 if race_data.growth_raw <= 100 else 0.15
            race = RaceProfile(
                name=player.name_singular or 'AI Race',
                growth_rate=growth,
                primary_trait=race_data.prt_name,
                native={
                    'prt_id': race_data.prt_id,
                    'lrts': list(race_data.lrts),
                    'lrt_mask': race_data.lrt_mask,
                    'mt_mask': race_data.mt_mask,
                    'population_efficiency_raw': race_data.population_efficiency_raw,
                    'economy_raw': list(race_data.economy_raw),
                    'research_cost_raw': list(race_data.research_cost_raw),
                    'flags_73': race_data.flags_73,
                    'spend_leftover_points_on_raw': race_data.spend_leftover_points_on_raw,
                    'hab_raw_hex': race_data.hab_raw.hex(' '),
                    'hab_center': list(race_data.hab_center),
                    'hab_low': list(race_data.hab_low),
                    'hab_high': list(race_data.hab_high),
                    'hab_immune': list(race_data.hab_immune),
                    'universal_hab': bool(race_data.universal_hab),
                    'player_relations': list(player.player_relations),
                },
            )
        else:
            tech = Tech()
            race = RaceProfile(name=player.name_singular or 'AI Race')

        player_designs=native.designs_for_player(player_id)
        base_designs={
            int(d.design_number):d
            for d in player_designs
            if d.is_starbase
        }

        planet_map = native.best_planets()
        planets: list[Planet] = []
        for pid, p in sorted(planet_map.items()):
            meta = native.planet_metadata.get(pid, {})
            # Observed means the player has at least environmental or concrete state data.
            observed = p.can_see_environment() or p.owner is not None or p.has_surface_minerals
            planets.append(Planet(
                id=pid,
                name=meta.get('name', f'Planet #{pid + 1}'),
                position=Position(float(meta.get('x', 0)), float(meta.get('y', 0))),
                owner=p.owner,
                habitability=_habitability_from_race(p, race_data),
                # StarsAPI PlanetBlock exact population is stored in HUNDREDS
                # of colonists. (Its popEstimate is in 400s and is converted to
                # exact units with population = 4 * popEstimate.)
                population=int(p.population or 0) * 100,
                factories=int(p.factories or 0),
                mines=int(p.mines or 0),
                defenses=int(p.defenses or 0),
                resources=0,
                ironium=int(p.ironium or 0),
                boranium=int(p.boranium or 0),
                germanium=int(p.germanium or 0),
                observed=observed,
                native={
                    'is_homeworld': p.is_homeworld,
                    'environment': [p.gravity, p.temperature, p.radiation],
                    'original_environment': [p.orig_gravity, p.orig_temperature, p.orig_radiation],
                    'mineral_concentrations': [p.ironium_conc, p.boranium_conc, p.germanium_conc],
                    'population_estimate': p.pop_estimate,
                    'defenses_estimate': p.defenses_estimate,
                    'has_scanner': p.has_scanner,
                    'has_starbase': p.has_starbase,
                    'starbase_design': p.starbase_design,
                    'starbase_hull_id': (
                        int(base_designs[p.starbase_design].hull_id)
                        if p.has_starbase and p.starbase_design in base_designs
                        else None
                    ),
                    'starbase_hull_name': (
                        base_designs[p.starbase_design].hull_name
                        if p.has_starbase and p.starbase_design in base_designs
                        else None
                    ),
                    'starbase_capabilities': starbase_capabilities(
                        int(base_designs[p.starbase_design].hull_id)
                        if p.has_starbase and p.starbase_design in base_designs
                        else None
                    ),
                    'has_route': p.has_route,
                    'route_short': p.route_short,
                    'observed_turn': p.observed_turn,
                    'is_terraformed': p.is_terraformed,
                    'has_artifact': p.has_artifact,
                    'contribute_only_leftover_resources_to_research': p.contribute_only_leftover_resources_to_research,
                    'habitability_estimate_method': 'native_race_envelope',
                    'population_raw_hundreds': int(p.population or 0),
                    'population_normalized': int(p.population or 0) * 100,
                    'population_source_year': int(native.header.get('year',2400)),
                    'population_observed_turn': p.observed_turn,
                },
            ))

        # .xy contains the whole galaxy. Add worlds absent from the M-file as explicitly unobserved
        # so scouting logic can reason about real unknown targets.
        seen_planet_ids = {p.id for p in planets}
        for pid, meta in sorted(native.planet_metadata.items()):
            if pid in seen_planet_ids:
                continue
            planets.append(Planet(
                id=pid,
                name=meta.get('name', f'Planet #{pid + 1}'),
                position=Position(float(meta.get('x', 0)), float(meta.get('y', 0))),
                owner=None, habitability=None, observed=False,
                native={'observed_turn': None, 'map_only': True},
            ))
        planets.sort(key=lambda p: p.id)

        design_roles = _design_roles(native, player_id)
        design_profiles={}
        design_profile_list=[]
        starbase_profile_list=[]
        for d in player_designs:
            if d.is_starbase:
                starbase_profile_list.append({
                    'design_number':int(d.design_number),
                    'name':d.name or d.hull_name,
                    'hull_id':int(d.hull_id),
                    'hull_name':d.hull_name,
                    'capabilities':starbase_capabilities(int(d.hull_id)),
                })
                continue
            fp=design_fuel_profile(d,role=design_roles.get(d.design_number,'unknown')).to_dict()
            fp['is_starbase']=False
            design_profiles[int(d.design_number)]=fp
            design_profile_list.append(fp)
        best_fleets = native.best_fleets()
        fleets: list[Fleet] = []
        for (owner, fid), f in sorted(best_fleets.items()):
            waypoints = native.waypoints_by_fleet.get(fid, []) if owner == player_id else []
            destination = None
            # IMPORTANT: f.warp / waypoint.warp is the CURRENT order speed, not
            # the fleet's maximum useful movement speed. Using it as a capability
            # ceiling caused fleets ordered at low warp once to remain permanently
            # slow. Preserve observed warp only as diagnostic state and give the
            # planner a normal mission-speed baseline.
            observed_warp = int(f.warp or 0)
            speed = 8
            if len(waypoints) >= 2:
                wp = waypoints[1]
                observed_warp = int(wp.warp or observed_warp)
                arrived = (int(f.x) == int(wp.x) and int(f.y) == int(wp.y))
                if wp.position_object_type == 17 and not arrived:
                    destination = wp.position_object
            role = _fleet_role(f.ship_count, design_roles) if owner == player_id else 'unknown'
            at_starbase=False
            if owner==player_id:
                at_starbase=any(
                    pp.owner==player_id
                    and bool(((pp.native or {}).get('starbase_capabilities') or {}).get('can_refuel'))
                    and (
                        int(f.position_object_id)==int(pp.id)
                        or (
                            abs(float(pp.position.x)-float(f.x))<=0.5
                            and abs(float(pp.position.y)-float(f.y))<=0.5
                        )
                    )
                    for pp in planets
                )
            class _Shim: pass
            shim=_Shim(); shim.ship_count=list(f.ship_count); shim.native={'ship_count':list(f.ship_count),'cargo':{'ironium':f.ironium,'boranium':f.boranium,'germanium':f.germanium,'population':f.population},'fuel':f.fuel}
            fuel_profile=fleet_fuel_profile(shim,design_profiles,at_starbase=at_starbase) if owner==player_id else {}
            fleets.append(Fleet(
                id=fid,
                name=f'Fleet #{fid + 1}',
                owner=owner,
                position=Position(float(f.x), float(f.y)),
                destination_planet_id=destination,
                role=role,
                # Fleet population cargo uses the same thousand-colonist unit.
                cargo_population=int(f.population or 0) * 1000,
                cargo_capacity=int(fuel_profile.get('cargo_capacity',0)),
                combat_power=float(f.mass or 0),
                speed=speed,
                native={
                    'kind': f.kind,
                    'position_object_id': f.position_object_id,
                    'ship_count': list(f.ship_count),
                    'ship_types_mask': f.ship_types_mask,
                    'cargo': {'ironium': f.ironium, 'boranium': f.boranium, 'germanium': f.germanium, 'population': f.population},
                    'fuel': f.fuel,
                    'damage': list(f.damaged_ship_info),
                    'battle_plan': f.battle_plan,
                    'waypoint_count': f.waypoint_count,
                    'waypoints': [
                        {
                            'x': int(w.x),
                            'y': int(w.y),
                            'position_object': int(w.position_object),
                            'warp': int(w.warp),
                            'task': int(w.waypoint_task),
                            'position_object_type': int(w.position_object_type),
                        }
                        for w in waypoints
                    ],
                    'population_raw_thousands': int(f.population or 0),
                    'observed_warp': observed_warp,
                    'mass': fuel_profile.get('mass',f.mass),
                    'fuel_profile': fuel_profile,
                    'cargo_capacity': int(fuel_profile.get('cargo_capacity',0)),
                    'cargo_capacity_confidence': fuel_profile.get('cargo_capacity_confidence','unknown'),
                    'race_fuel_flags': {'ife':has_ife(race),'ce':has_ce(race)},
                    'at_starbase': at_starbase,
                },
            ))

        return GameState(
            game_name=native.game_name or m_path.stem,
            year=int(native.header.get('year', 2400)),
            player_id=player_id,
            race=race,
            tech=tech,
            planets=planets,
            fleets=fleets,
            messages=[],
            native={
                'header': dict(native.header),
                'battle_plans': [asdict(x) for x in native.battle_plans],
                'objects': [asdict(x) for x in native.objects],
                'production_by_planet': {str(k): [asdict(x) for x in v] for k, v in native.production_by_planet.items()},
                'designs': [asdict(x) for x in native.designs_for_player(player_id)],
                'design_profiles': design_profile_list,
                'starbase_profiles': starbase_profile_list,
                'population_source_m_file': str(m_path),
                'population_source_year': int(native.header.get('year',2400)),
                'block_inventory': list(native.block_inventory),
            },
        )

    def write_orders(self, orders: OrderSet, path: Path) -> None:
        import json
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(orders.to_dict(), indent=2), encoding='utf-8')
