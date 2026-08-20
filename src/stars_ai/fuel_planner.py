
from __future__ import annotations
from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path
import csv
from .util import distance
from .population_units import COLONY_LOAD_KT

ENGINE=1; SCANNER=2; SHIELD=4; ARMOR=8; BEAM=16; TORPEDO=32; BOMB=64
MINING_ROBOT=128; MINE_LAYER=256; ORBITAL=512; PLANETARY=1024; ELECTRICAL=2048; MECHANICAL=4096

ENGINE_DATA = {
    0:("Settler's Delight",2,(0,0,0,0,0,0,0,140,275,480,576),False),
    1:("Quick Jump 5",4,(0,0,25,100,100,100,180,500,800,900,1080),False),
    2:("Fuel Mizer",6,(0,0,0,0,0,35,120,175,235,360,420),False),
    3:("Long Hump 6",9,(0,0,20,60,100,100,105,450,750,900,1080),False),
    4:("Daddy Long Legs 7",13,(0,0,20,60,70,100,100,110,600,750,900),False),
    5:("Alpha Drive 8",17,(0,0,15,50,60,70,100,100,115,700,840),False),
    6:("Trans-Galactic Drive",25,(0,0,15,35,45,55,70,80,90,100,120),False),
    7:("Interspace-10",25,(0,0,10,30,40,50,60,70,80,90,100),False),
    8:("Enigma Pulsar",20,(0,0,0,0,0,0,65,75,85,95,105),True),
    9:("Trans-Star 10",5,(0,0,5,15,20,25,30,35,40,45,50),False),
    10:("Radiating Hydro-Ram Scoop",10,(0,0,0,0,0,0,0,165,375,600,720),True),
    11:("Sub-Galactic Fuel Scoop",20,(0,0,0,0,0,0,85,105,210,380,456),True),
    12:("Trans-Galactic Fuel Scoop",19,(0,0,0,0,0,0,0,88,100,145,174),True),
    13:("Trans-Galactic Super Scoop",18,(0,0,0,0,0,0,0,0,65,90,108),True),
    14:("Trans-Galactic Mizer Scoop",11,(0,0,0,0,0,0,0,0,0,70,84),True),
    15:("Galaxy Scoop",8,(0,0,0,0,0,0,0,0,0,0,60),True),
}

COMPONENT_MASSES={
 ENGINE:{k:v[1] for k,v in ENGINE_DATA.items()},
 SCANNER:dict(enumerate([2,5,2,2,3,15,6,2,4,5,2,4,6,3,20,4])),
 SHIELD:dict(enumerate([1,1,1,10,2,1,10,1,1,1])),
 ARMOR:dict(enumerate([60,56,25,54,15,50,50,50,45,20,40,30])),
 BEAM:dict(enumerate([1,1,3,1,10,2,1,2,3,1,10,2,1,2,3,1,10,2,8,1,2,3,1,2])),
 TORPEDO:dict(enumerate([25,25,25,25,25,25,25,8,35,35,35,35])),
 BOMB:dict(enumerate([40,45,50,55,52,30,35,45,5,45,50,57,64,55,50])),
 MINING_ROBOT:dict(enumerate([80,240,240,240,240,80,20,80])),
 MINE_LAYER:dict(enumerate([25,30,30,30,10,15,20,100,135,140])),
 MECHANICAL:dict(enumerate([32,50,5,7,9,3,8,5,5,10,1])),
 ELECTRICAL:dict(enumerate([1,2,3,5,2,1,1,1,1,1,1,1,1,1,2,1,10])),
}
CATEGORY_MAX_MASS={ENGINE:25,SCANNER:20,SHIELD:10,ARMOR:60,BEAM:10,TORPEDO:35,BOMB:64,MINING_ROBOT:240,MINE_LAYER:140,ORBITAL:1600,PLANETARY:5000,ELECTRICAL:10,MECHANICAL:50}

@dataclass(frozen=True)
class HullFuelSpec:
    hull_id:int; name:str; base_mass:int; base_cargo:int; base_fuel:int; engine_count:int

@dataclass
class DesignFuelProfile:
    design_number:int; name:str; hull_id:int; role:str; dry_mass:int; cargo_capacity:int; fuel_capacity:int
    engine_id:int|None; engine_name:str; ram_scoop:bool; mass_confidence:str; components:list[dict]
    def to_dict(self): return asdict(self)

@lru_cache(maxsize=1)
def stock_hull_fuel_specs():
    path=Path(__file__).with_name('data_hulls.mod'); out={}
    for parts in csv.reader(path.read_text(encoding='latin-1').splitlines()):
        if not parts or int(parts[0])!=15: continue
        nums=[int(x) if x else 0 for x in parts[3:]]; hid=int(nums[0])
        out[hid]=HullFuelSpec(hid,parts[2],int(nums[7]),int(nums[13]),int(nums[14]),int(nums[17]))
    return out

def _slot_values(s):
    if isinstance(s,dict): return int(s.get('category',0)),int(s.get('item_id',0)),int(s.get('count',0))
    return int(s.category),int(s.item_id),int(s.count)

def component_mass(category,item_id):
    v=COMPONENT_MASSES.get(category,{}).get(item_id)
    return (int(v),True) if v is not None else (int(CATEGORY_MAX_MASS.get(category,100)),False)

def design_fuel_profile(design,role='unknown'):
    hid=int(design.hull_id if hasattr(design,'hull_id') else design['hull_id'])
    h=stock_hull_fuel_specs().get(hid,HullFuelSpec(hid,f'Hull#{hid}',100,0,0,1))
    name=str(getattr(design,'name','') or (design.get('name','') if isinstance(design,dict) else '') or h.name)
    num=int(design.design_number if hasattr(design,'design_number') else design['design_number'])
    slots=design.slots if hasattr(design,'slots') else design.get('slots',[])
    mass=h.base_mass; fuel=h.base_fuel; eid=None; ename='Unknown Engine'; ram=False; exact=True; comps=[]
    for s in slots:
        cat,item,count=_slot_values(s)
        if count<=0: continue
        m,ok=component_mass(cat,item); exact=exact and ok; mass+=m*count
        comps.append({'category':cat,'item_id':item,'count':count,'mass_each':m,'mass_exact':ok})
        if cat==ENGINE and item in ENGINE_DATA:
            eid=item; ename=ENGINE_DATA[item][0]; ram=ENGINE_DATA[item][3]
        if cat==MECHANICAL and item==5: fuel+=250*count
        elif cat==MECHANICAL and item==6: fuel+=500*count
        elif cat==ELECTRICAL and item==16: fuel+=200*count
    return DesignFuelProfile(num,name,hid,role,mass,int(h.base_cargo),fuel,eid,ename,ram,'exact-stock' if exact else 'conservative-stock',comps)

def _fun(eid,warp):
    if eid in ENGINE_DATA: return int(ENGINE_DATA[eid][2][max(0,min(10,int(warp)))])
    return (0,0,25,60,100,120,180,500,800,950,1100)[max(0,min(10,int(warp)))]

def fleet_fuel_profile(fleet,design_profiles,at_starbase=False):
    counts=fleet.ship_count if hasattr(fleet,'ship_count') else (fleet.native or {}).get('ship_count',[])
    groups=[]; dry=0; cap=0; cargo_cap=0; all_ram=True
    for slot,count in enumerate(counts):
        if not count: continue
        dp=design_profiles.get(slot)
        if not dp: continue
        gm=int(dp['dry_mass'])*int(count); dry+=gm; cap+=int(dp['fuel_capacity'])*int(count); cargo_cap+=int(dp.get('cargo_capacity',0))*int(count)
        all_ram=all_ram and bool(dp.get('ram_scoop'))
        groups.append({'design_slot':slot,'count':int(count),'mass':gm,'engine_id':dp.get('engine_id'),'engine_name':dp.get('engine_name'),'design_name':dp.get('name')})
    native=getattr(fleet,'native',{}) or {}; cargo=native.get('cargo',{})
    cargo_mass=sum(int(cargo.get(k,0) or 0) for k in ('ironium','boranium','germanium','population'))
    cur=int(native.get('fuel',0) or 0)
    return {'dry_mass':dry,'cargo_mass':cargo_mass,'mass':dry+cargo_mass,'cargo_capacity':cargo_cap,'cargo_capacity_confidence':'base-hull-only','fuel':cur,'fuel_capacity':cap,'effective_fuel':max(cur,cap) if at_starbase else cur,'at_starbase':bool(at_starbase),'all_ram_scoop':bool(groups) and all_ram,'groups':groups}

def has_ife(race): return 'IFE' in set((getattr(race,'native',{}) or {}).get('lrts',[]))
def has_ce(race): return 'CE' in set((getattr(race,'native',{}) or {}).get('lrts',[]))


def highest_zero_fuel_warp(profile, max_warp=9):
    """
    Highest warp at which every engine group in the fleet consumes zero fuel.
    Fuel Mizer scouts therefore report Warp 4; later scoop engines may report
    higher free-cruise speeds.
    """
    groups=list(profile.get('groups',[]) or [])
    if not groups:
        return 0
    for w in range(min(9,int(max_warp)),0,-1):
        if all(_fun(g.get('engine_id'),w) == 0 for g in groups):
            return w
    return 0


def scout_one_way_budget(profile):
    """
    Recon probes are expendable strategic sensors, not round-trip transports.
    Preserve only a tiny navigation margin; do not reserve fuel for returning
    to a starbase.
    """
    avail=float(profile.get('effective_fuel',profile.get('fuel',0)) or 0)
    cap=float(profile.get('fuel_capacity',0) or 0)
    return max(0.0,avail-max(1.0,.01*cap))


def scout_one_way_warp(profile,distance_ly,ife=False,ce=False,pressure=1.0):
    """
    Efficient routine recon warp.

    - Fuel Mizer / free-cruise fleets: Warp 4 indefinitely, or Warp 5 while
      there is comfortable fuel for the leg.
    - Conventional scouts: cap routine exploration at Warp 7 to avoid the
      observed Warp-9 fuel burn/refuel ping-pong.
    - No return reserve is charged.
    """
    d=float(distance_ly)
    if d<=.01:
        return 0
    free=highest_zero_fuel_warp(profile)
    budget=scout_one_way_budget(profile)
    cap=float(profile.get('fuel_capacity',0) or 0)
    avail=float(profile.get('effective_fuel',profile.get('fuel',0)) or 0)
    fuel_ratio=(avail/cap) if cap>0 else 0.0

    if free >= 4:
        # Fuel Mizer case: Warp 5 is a useful default while fuel is healthy.
        # Once fuel is low, fall back to truly free Warp 4 and never require a
        # return-to-base merely to continue exploring.
        candidate=min(5, 6 if float(pressure)>=1.9 else 5)
        candidate=min(candidate,6) if ce else candidate
        if candidate > free and estimate_fuel(profile,d,candidate,ife=ife) <= budget and fuel_ratio >= .20:
            return candidate
        return min(free,6) if ce else free

    # Conventional probe: efficient cruise, not maximum possible warp.
    wcap=6 if ce else 7
    for w in range(wcap,1,-1):
        if estimate_fuel(profile,d,w,ife=ife) <= budget+1e-9:
            return w
    for w in range(wcap,1,-1):
        if estimate_fuel(profile,d,w,ife=ife) <= 1e-9:
            return w
    return None


def scout_one_way_reachable(profile,distance_ly,ife=False,ce=False):
    return scout_one_way_warp(
        profile,distance_ly,ife=ife,ce=ce,pressure=1.0
    ) is not None


def profile_after_scout_leg(profile,distance_ly,warp,ife=False):
    """Return a shallow simulated fuel profile after a recon leg."""
    out=dict(profile)
    burn=estimate_fuel(profile,distance_ly,warp,ife=ife)
    cur=float(profile.get('effective_fuel',profile.get('fuel',0)) or 0)
    remaining=max(0.0,cur-burn)
    out['fuel']=remaining
    out['effective_fuel']=remaining
    return out

def estimate_fuel(profile,distance_ly,warp,ife=False):
    d=max(0.0,float(distance_ly)); w=max(0,min(10,int(warp))); factor=.85 if ife else 1.0
    per=0.0; worst=0
    for g in profile.get('groups',[]):
        fun=_fun(g.get('engine_id'),w); worst=max(worst,fun); per+=(float(g.get('mass',0))/200.0)*(fun/100.0)
    cargo=float(profile.get('cargo_mass',0))
    if cargo: per+=(cargo/200.0)*(worst/100.0)
    return d*per*factor

def _return_reserve(profile,d,mission,ife):
    m=str(mission or '').lower(); cap=float(profile.get('fuel_capacity',0) or 0)
    if m in ('colonize','reposition_for_colonize','refuel','return_for_colonists'): return max(1.0,.01*cap)
    if m in ('scan','recon'):
        return max(1.0,.01*cap)
    factor=1.0 if ('mining' in m or 'transport' in m) else .5 if ('combat' in m or 'attack' in m or 'intercept' in m) else .25
    return max(.05*cap, estimate_fuel(profile,d,3,ife=ife)*factor)

def fastest_fuel_safe_warp(profile,distance_ly,mission,ife=False,ce=False,max_warp=9):
    d=float(distance_ly)
    if d<=.01: return 0
    avail=float(profile.get('effective_fuel',profile.get('fuel',0)) or 0); cap=float(profile.get('fuel_capacity',0) or 0)
    m=str(mission or '').lower(); routine=not any(x in m for x in ('attack','intercept','emergency','retreat'))
    wcap=min(9,int(max_warp)); wcap=min(wcap,6) if ce and routine else wcap
    reserve=_return_reserve(profile,d,m,ife); budget=max(0.0,avail-max(1.0,.01*cap)-reserve)
    for w in range(wcap,1,-1):
        if estimate_fuel(profile,d,w,ife=ife)<=budget+1e-9: return w
    for w in range(wcap,1,-1):
        if estimate_fuel(profile,d,w,ife=ife)<=1e-9: return w
    return None


def profile_with_planned_cargo(profile,load):
    """Clone a fuel profile and include the mass of cargo this order will load."""
    out=dict(profile)
    if not load:
        return out
    added=sum(
        max(0,int(load.get(k,0) or 0))
        for k in ("ironium","boranium","germanium","population")
    )
    out["cargo_mass"]=int(profile.get("cargo_mass",0) or 0)+added
    out["mass"]=int(profile.get("dry_mass",0) or 0)+out["cargo_mass"]
    return out

def mission_reachable(fleet,target_position,mission):
    n=getattr(fleet,'native',{}) or {}; fp=n.get('fuel_profile')
    if not fp: return True
    flags=n.get('race_fuel_flags',{})
    m=str(mission or '').lower()
    d=distance(fleet.position,target_position)
    if m in ('scan','recon'):
        return scout_one_way_reachable(
            fp,d,ife=bool(flags.get('ife')),ce=bool(flags.get('ce'))
        )
    return fastest_fuel_safe_warp(fp,d,m,ife=bool(flags.get('ife')),ce=bool(flags.get('ce'))) is not None


def mission_reachable_with_planned_cargo(fleet,target_position,mission,load):
    """Reachability after cargo that will be loaded by the same order.

    Cargo values use Stars!' native kT units. In particular, a 2,500-colonist
    colony packet is 25 kT of population cargo. Evaluating an empty colony ship
    without this mass can approve a route that becomes unsafe as soon as the
    load block executes.
    """
    native=getattr(fleet,'native',{}) or {}
    profile=native.get('fuel_profile')
    if not profile:
        return True
    flags=native.get('race_fuel_flags',{})
    planned=profile_with_planned_cargo(profile,load)
    d=distance(fleet.position,target_position)
    return fastest_fuel_safe_warp(
        planned,d,mission,
        ife=bool(flags.get('ife')),
        ce=bool(flags.get('ce')),
    ) is not None

def best_range_ly(dp,warp=8,ife=False):
    fp={'groups':[{'mass':int(dp.get('dry_mass',0)),'engine_id':dp.get('engine_id')}],'cargo_mass':0,'fuel_capacity':int(dp.get('fuel_capacity',0)),'fuel':int(dp.get('fuel_capacity',0)),'effective_fuel':int(dp.get('fuel_capacity',0)),'all_ram_scoop':bool(dp.get('ram_scoop'))}
    per=estimate_fuel(fp,1,warp,ife=ife)
    return 1000000.0 if per<=1e-9 else fp['fuel_capacity']/per


def apply_fuel_safety(state,orders):
    bases=[
        p for p in state.planets
        if p.owner==state.player_id
        and bool(((p.native or {}).get('starbase_capabilities') or {}).get('can_refuel'))
    ]
    planets={p.id:p for p in state.planets}
    fleets={f.id:f for f in state.fleets if f.owner==state.player_id}
    watchdog=(state.native or {}).get('strategic_watchdog') or {}
    recon_pressure=float(watchdog.get('exploration_pressure',1.0))
    kept=[]; extras=[]

    for o in orders.orders:
        if o.kind not in ('move_fleet','colony_operation','transport_minerals'):
            kept.append(o); continue

        fid=int(o.payload.get('fleet_id',-1))
        f=fleets.get(fid)
        target=planets.get(int(o.payload.get('destination_planet_id',-1)))
        if not f or not target or not (f.native or {}).get('fuel_profile'):
            kept.append(o); continue

        fp=f.native['fuel_profile']
        if o.kind=='transport_minerals':
            fp=profile_with_planned_cargo(fp,o.payload.get('load'))
        elif o.kind=='colony_operation' and o.payload.get('load_25kt_population'):
            fp=profile_with_planned_cargo(
                fp,
                {'population':int(o.payload.get('load_population_kt',COLONY_LOAD_KT))},
            )
        flags=(f.native or {}).get('race_fuel_flags',{})
        mission=str(o.payload.get('mission',o.kind))
        ml=mission.lower()
        d=distance(f.position,target.position)
        cap=float(fp.get('fuel_capacity',0) or 0)
        cur=float(fp.get('fuel',0) or 0)

        # v7.1: reconnaissance is a one-way campaign. Never replace a valid scan
        # with an automatic refuel detour. The exploration router itself may
        # explicitly create refuel_for_scan only when the detour unlocks a
        # valuable multi-world route.
        if ml in ('scan','recon'):
            safe=scout_one_way_warp(
                fp,d,
                ife=bool(flags.get('ife')),
                ce=bool(flags.get('ce')),
                pressure=recon_pressure,
            )
            if safe is None:
                f.native['fuel_blocked']=True
                f.native['fuel_block_reason']=(
                    f'No one-way reconnaissance route to {target.name}; '
                    f'fuel={cur:.0f}/{cap:.0f}, mass={fp.get("mass","?")}.'
                )
                orders.notes.append(
                    f'RECON FUEL BLOCK: {f.name} -> {target.name}: '
                    f'{f.native["fuel_block_reason"]}'
                )
                continue

            requested=int(o.payload.get('warp',safe) or safe)
            o.payload['warp']=int(safe)
            route_waypoints=o.payload.get('route_waypoints') or []
            if route_waypoints:
                route_waypoints[0]['warp']=int(safe)
            o.payload['fuel_plan']={
                'policy':'one_way_probe',
                'requested_warp':requested,
                'selected_warp':int(safe),
                'distance':round(d,2),
                'fuel':round(cur,2),
                'capacity':round(cap,2),
                'mass':fp.get('mass'),
                'estimated_fuel':round(
                    estimate_fuel(fp,d,safe,ife=bool(flags.get('ife'))),2
                ),
                'free_cruise_warp':highest_zero_fuel_warp(fp),
                'return_reserve':0,
                'engine_names':sorted({
                    g.get('engine_name') for g in fp.get('groups',[])
                    if g.get('engine_name')
                }),
                'all_ram_scoop':bool(fp.get('all_ram_scoop')),
            }
            if safe<requested:
                o.reason+=(
                    f' Recon cruise reduced Warp {requested}->{safe} to maximize '
                    f'probe lifetime over {d:.1f} ly.'
                )
            kept.append(o)
            continue

        safe=fastest_fuel_safe_warp(
            fp,d,mission,
            ife=bool(flags.get('ife')),
            ce=bool(flags.get('ce')),
        )
        low=cap>0 and cur/cap<.15 and not fp.get('all_ram_scoop') and not fp.get('at_starbase')

        if (safe is None or low) and not ml.startswith('refuel'):
            choices=[]
            for b in bases:
                bd=distance(f.position,b.position)
                if bd<=.5: continue
                bw=fastest_fuel_safe_warp(
                    fp,bd,'refuel',
                    ife=bool(flags.get('ife')),
                    ce=bool(flags.get('ce')),
                )
                if bw is not None:
                    choices.append((bd,b,bw))
            if choices:
                bd,b,bw=min(choices,key=lambda x:x[0])
                from .models import Order
                extras.append(Order(
                    'move_fleet',
                    {
                        'fleet_id':fid,
                        'destination_planet_id':b.id,
                        'warp':bw,
                        'mission':f'refuel_for_{mission}',
                        'deferred_destination_planet_id':target.id,
                        'fuel_plan':{
                            'reason':'low_fuel_refuel' if low else 'target_not_fuel_reachable',
                            'fuel':cur,'capacity':cap,'mass':fp.get('mass'),
                            'original_distance':round(d,2),
                            'refuel_distance':round(bd,2),
                        },
                    },
                    f'Fuel safety: defer {mission} to {target.name}; route to starbase {b.name} first for refueling.',
                    max(int(o.priority),140),
                ))
                continue

        if safe is None:
            f.native['fuel_blocked']=True
            f.native['fuel_block_reason']=(
                f'No fuel-safe Warp 2+ route to {target.name}; '
                f'fuel={cur:.0f}/{cap:.0f}, mass={fp.get("mass","?")}.'
            )
            orders.notes.append(
                f'FUEL BLOCK: {f.name} -> {target.name}: {f.native["fuel_block_reason"]}'
            )
            continue

        requested=int(o.payload.get('warp',safe) or safe)
        o.payload['warp']=safe
        o.payload['fuel_plan']={
            'requested_warp':requested,
            'selected_warp':safe,
            'distance':round(d,2),
            'fuel':round(cur,2),
            'capacity':round(cap,2),
            'mass':fp.get('mass'),
            'estimated_fuel':round(
                estimate_fuel(fp,d,safe,ife=bool(flags.get('ife'))),2
            ),
            'engine_names':sorted({
                g.get('engine_name') for g in fp.get('groups',[])
                if g.get('engine_name')
            }),
            'all_ram_scoop':bool(fp.get('all_ram_scoop')),
        }
        if safe<requested:
            o.reason+=(
                f' Fuel planner reduced Warp {requested}->{safe} for {d:.1f} ly '
                f'with {cur:.0f}/{cap:.0f} mg fuel and ~{fp.get("mass",0)} kt mass.'
            )
        kept.append(o)

    orders.orders=kept+extras
