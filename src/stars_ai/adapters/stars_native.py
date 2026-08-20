from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..planet_names import get_planet_name

BLOCK_NAMES = {
    0:'FileFooter',1:'ManualSmallLoadUnloadTask',2:'ManualMediumLoadUnloadTask',3:'WaypointDelete',
    4:'WaypointAdd',5:'WaypointChangeTask',6:'Player',7:'Planets',8:'FileHeader',9:'FileHash',
    10:'WaypointRepeatOrders',11:'Unknown11',12:'Events',13:'Planet',14:'PartialPlanet',15:'Unknown15',
    16:'Fleet',17:'PartialFleet',18:'Unknown18',19:'WaypointTask',20:'Waypoint',21:'FleetName',22:'Unknown22',
    23:'MoveShips',24:'FleetSplit',25:'ManualLargeLoadUnloadTask',26:'Design',27:'DesignChange',28:'ProductionQueue',
    29:'ProductionQueueChange',30:'BattlePlan',31:'Battle',32:'Counters',33:'MessagesFilter',34:'ResearchChange',
    35:'PlanetChange',36:'ChangePassword',37:'FleetsMerge',38:'PlayersRelationChange',39:'BattleContinuation',
    40:'Message',41:'AiHFileRecord',42:'SetFleetBattlePlan',43:'Object',44:'RenameFleet',45:'PlayerScores',46:'SaveAndSubmit'
}

# Known names needed by the included real-game fixture. Unknown name IDs remain lossless as "PlanetName#<id>".
# This can later be replaced by the complete canonical Stars! name table.
KNOWN_PLANET_NAMES = {
    473:'Knob', 538:'Magellan', 720:'Quiche', 797:'Serapa'
}

PRIMES = [
    3,5,7,11,13,17,19,23,29,31,37,41,43,47,53,59,
    61,67,71,73,79,83,89,97,101,103,107,109,113,127,131,137,
    139,149,151,157,163,167,173,179,181,191,193,197,199,211,223,227,
    229,233,239,241,251,257,263,279,271,277,281,283,293,307,311,313,
]

@dataclass
class FileHeader:
    game_id:int
    turn:int
    year:int
    salt:int
    player_index:int
    flags:int
    shareware:bool
    file_type:int = 0
    turn_submitted:bool = False
    host_using:bool = False
    multiple_turns:bool = False
    game_over:bool = False
    unknown_bits:int = 0

@dataclass
class NativeBlock:
    type_id:int; size:int; data:bytes
    @property
    def name(self): return BLOCK_NAMES.get(self.type_id, f'Block{self.type_id}')

@dataclass
class XYPlanet:
    planet_id:int; display_id:int; name_id:int; name:str; x:int; y:int

class StarsRandom:
    def __init__(self, seed_a:int, seed_b:int, rounds:int):
        self.seed_a=seed_a; self.seed_b=seed_b
        for _ in range(rounds): self.next_random()
    def next_random(self)->int:
        new_a=(self.seed_a % 53668)*40014 - (self.seed_a // 53668)*12211
        new_b=(self.seed_b % 52774)*40692 - (self.seed_b // 52774)*3791
        if new_a < 0: new_a += 0x7fffffab
        if new_b < 0: new_b += 0x7fffff07
        self.seed_a,self.seed_b=new_a,new_b
        return (self.seed_a-self.seed_b) & 0xffffffff


def _u16(b:bytes, o:int=0)->int: return int.from_bytes(b[o:o+2], 'little')
def _u32(b:bytes, o:int=0)->int: return int.from_bytes(b[o:o+4], 'little')
def _read_n(b:bytes, o:int, n:int)->int:
    if n == 0: return 0
    return int.from_bytes(b[o:o+n], 'little')
def _content_len(code:int)->int:
    # Stars! encodes 0/1/2/4 bytes as 0/1/2/3.
    return (0,1,2,4)[code & 3]


def parse_file_header(data:bytes)->FileHeader:
    game_id=_u32(data,4)
    turn=_u16(data,10)
    pd=_u16(data,12)
    file_type=data[14]
    flags=data[15]
    return FileHeader(
        game_id=game_id,
        turn=turn,
        year=2400+turn,
        salt=pd>>5,
        player_index=pd&0x1f,
        flags=flags,
        shareware=bool(flags & 0x10),
        file_type=file_type,
        turn_submitted=bool(flags & 0x01),
        host_using=bool(flags & 0x02),
        multiple_turns=bool(flags & 0x04),
        game_over=bool(flags & 0x08),
        unknown_bits=(flags >> 5) & 0x07,
    )


def _rng(header:FileHeader)->StarsRandom:
    i1=header.salt & 0x1f; i2=(header.salt >> 5) & 0x1f
    if (header.salt >> 10) == 1: i1 += 32
    else: i2 += 32
    rounds=((header.game_id & 3)+1)*((header.turn & 3)+1)*((header.player_index & 3)+1)+(1 if header.shareware else 0)
    return StarsRandom(PRIMES[i1],PRIMES[i2],rounds)


def _crypt(data:bytes, rng:StarsRandom)->bytes:
    n=len(data); padded=data + b'\0'*((-n)%4); out=bytearray()
    for i in range(0,len(padded),4):
        v=int.from_bytes(padded[i:i+4],'little') ^ rng.next_random()
        out += v.to_bytes(4,'little')
    return bytes(out[:n])


def read_blocks(path:str|Path)->tuple[FileHeader,list[NativeBlock],bytes|None]:
    raw=Path(path).read_bytes(); off=0; rng=None; header=None; out=[]; xy_extra=None
    while off < len(raw):
        if off+2 > len(raw): raise ValueError(f'Truncated Stars! block header at {off}')
        bh=_u16(raw,off); type_id=bh>>10; size=bh & 0x3ff
        enc=raw[off+2:off+2+size]; off += 2+size
        if len(enc) != size: raise ValueError(f'Truncated Stars! block {type_id}')
        if type_id == 8:
            dec=enc; header=parse_file_header(dec); rng=_rng(header)
        else:
            if rng is None: raise ValueError('Encrypted block found before FileHeader')
            dec=_crypt(enc,rng)
        out.append(NativeBlock(type_id,size,dec))
        if type_id == 7:
            count=_u16(dec,10); extra_len=count*4
            xy_extra=raw[off:off+extra_len]
            if len(xy_extra)!=extra_len: raise ValueError('Truncated XY planet coordinate table')
            off += extra_len
    if header is None: raise ValueError('No Stars! FileHeader block found')
    return header,out,xy_extra


def parse_xy(path:str|Path)->dict:
    header,blocks,extra=read_blocks(path)
    pb=next((b for b in blocks if b.type_id==7),None)
    if pb is None or extra is None: raise ValueError('No Planets block in .xy')
    d=pb.data; count=_u16(d,10); x=1000; planets=[]
    for i in range(count):
        v=_u32(extra,i*4); name_id=v>>22; y=(v>>10)&0xfff; x += v&0x3ff
        planets.append(XYPlanet(i,i+1,name_id,get_planet_name(name_id),x,y))
    return {
        'header':header,
        'game_name':d[32:64].split(b'\0',1)[0].decode('latin1','replace'),
        'universe_size':_u16(d,4),'density':_u16(d,6),'player_count':_u16(d,8),
        'planet_count':count,'starting_distance':_u32(d,12),'game_settings':_u16(d,16),
        'planets':planets,
    }


def parse_planet_block(block:NativeBlock)->dict:
    d=block.data
    planet=(d[0] | ((d[1]&7)<<8)); owner=(d[1]&0xf8)>>3; owner=None if owner==31 else owner+1
    flags=_u16(d,2); i=4
    home=bool(flags&0x80); in_use=bool(flags&0x04); has_env=bool(flags&0x02); bit1=bool(flags&0x01)
    has_surface=bool(flags&0x2000); has_inst=bool(flags&0x0800); terra=bool(flags&0x0400); has_sb=bool(flags&0x0200)
    can_see_env = has_env or ((has_surface or in_use) and not bit1)
    out={'planet_id':planet,'display_id':planet+1,'owner':owner,'is_homeworld':home,'flags':flags,
         'has_environment':can_see_env,'has_surface_minerals':has_surface,'has_installations':has_inst,'has_starbase':has_sb}
    if can_see_env:
        pre=d[i]; plen=1+((pre&0x30)>>4)+((pre&0x0c)>>2)+(pre&3); i += plen
        out['mineral_concentration']={'ironium':d[i],'boranium':d[i+1],'germanium':d[i+2]}; i+=3
        out['environment']={'gravity':d[i],'temperature':d[i+1],'radiation':d[i+2]}; i+=3
        if terra:
            out['original_environment']={'gravity':d[i],'temperature':d[i+1],'radiation':d[i+2]}; i+=3
        if owner is not None:
            est=_u16(d,i); i+=2
            out['defense_estimate_16ths']=est//4096; out['population_estimate_hundreds']=est%4096
    if has_surface:
        lens=d[i]; i+=1
        ls=[_content_len((lens>>shift)&3) for shift in (0,2,4,6)]
        vals=[]
        for n in ls: vals.append(_read_n(d,i,n)); i+=n
        out['surface']={'ironium':vals[0],'boranium':vals[1],'germanium':vals[2],
                        'population_hundreds':vals[3],'population':vals[3]*100}
    if has_inst:
        inst=d[i:i+8]; i+=8
        mines=inst[1] | ((inst[2]&0x0f)<<8)
        factories=((inst[2]&0xf0)>>4) | (inst[3]<<4)
        out['installations']={'excess_population':inst[0],'mines':mines,'factories':factories,
                              'defenses':inst[4],'has_scanner':(inst[6]&1)==0,
                              'leftover_resources_to_research':bool(inst[6]&0x80)}
    if has_sb:
        if block.type_id == 14:
            out['starbase_design']=d[i] & 0x0f; i+=1
        else:
            out['starbase_design']=d[i]&0x0f; out['starbase_raw']=d[i:i+4].hex(); i+=4
    return out


def parse_fleet_block(block:NativeBlock)->dict:
    d=block.data
    fleet=d[0] | ((d[1]&1)<<8); owner=(d[1]>>1)+1; kind=d[4]; byte5=d[5]
    pos_id=_u16(d,6); x=_u16(d,8); y=_u16(d,10); ship_types=_u16(d,12); i=14
    two=(byte5&8)==0; counts={}
    for bit in range(16):
        if ship_types & (1<<bit):
            n=_u16(d,i) if two else d[i]; i += 2 if two else 1; counts[bit]=n
    out={'fleet_id':fleet,'display_id':fleet+1,'owner':owner,'kind':kind,'position_object_id':pos_id,'x':x,'y':y,'ship_counts_by_design_slot':counts}
    if kind in (4,7):
        lens=_u16(d,i); i+=2
        codes=[(lens>>s)&3 for s in (0,2,4,6)] + [(lens>>8)&3]
        vals=[]
        for c in codes:
            n=_content_len(c); vals.append(_read_n(d,i,n)); i+=n
        out['cargo']={'ironium':vals[0],'boranium':vals[1],'germanium':vals[2],
                      'population_hundreds':vals[3],'population':vals[3]*100,'fuel':vals[4]}
    if kind==7:
        damaged=_u16(d,i); i+=2
        damaged_info={}
        for bit in range(16):
            if damaged&(1<<bit): damaged_info[bit]=_u16(d,i); i+=2
        out['damaged']=damaged_info; out['battle_plan']=d[i]; out['waypoint_count']=d[i+1]; i+=2
    else:
        out['delta_x']=d[i]; out['delta_y']=d[i+1]; out['warp']=d[i+2]&15; i+=4
        out['mass']=_u32(d,i); i+=4
    return out


def parse_waypoint(block:NativeBlock)->dict:
    d=block.data
    return {'x':_u16(d,0),'y':_u16(d,2),'position_object_id':_u16(d,4),'warp':d[6]>>4,
            'task':d[6]&0x0f,'position_object_type':d[7], 'additional_bytes':d[8:].hex()}


def parse_player(block:NativeBlock)->dict:
    d=block.data
    return {'player_number':d[0]+1,'ship_design_count':d[1], 'planet_count':d[2]|((d[3]&3)<<8),
            'fleet_count':d[4]|((d[5]&3)<<8), 'starbase_design_count':(d[5]&0xf0)>>4,
            'logo':d[6]>>3,'full_data':bool(d[6]&4)}


def inspect_m_file(m_path:str|Path, xy_path:str|Path)->dict:
    xy=parse_xy(xy_path); coords={p.planet_id:p for p in xy['planets']}
    h,bs,_=read_blocks(m_path); players=[]; planets=[]; fleets=[]; waypoints=[]
    for b in bs:
        if b.type_id==6: players.append(parse_player(b))
        elif b.type_id in (13,14):
            p=parse_planet_block(b); meta=coords.get(p['planet_id'])
            if meta: p.update(name=meta.name,name_id=meta.name_id,x=meta.x,y=meta.y)
            planets.append(p)
        elif b.type_id in (16,17): fleets.append(parse_fleet_block(b))
        elif b.type_id in (19,20): waypoints.append(parse_waypoint(b))
    # Waypoint blocks follow full fleet blocks in fleet order; attach sequentially by waypoint_count.
    wi=0
    for f in fleets:
        n=f.get('waypoint_count',0); f['waypoints']=waypoints[wi:wi+n]; wi += n
        if f.get('position_object_id') in coords:
            p=coords[f['position_object_id']]; f['position_planet_name']=p.name; f['position_planet_display_id']=p.display_id
    return {'header':header_dict(h),'xy':{'game_name':xy['game_name'],'planet_count':xy['planet_count'],'player_count':xy['player_count']},
            'players':players,'planets':planets,'fleets':fleets,
            'block_inventory':[{'type_id':b.type_id,'name':b.name,'size':b.size} for b in bs]}


def decode_x_orders(x_path:str|Path, xy_path:str|Path)->dict:
    xy=parse_xy(xy_path); coords={p.planet_id:p for p in xy['planets']}
    h,bs,_=read_blocks(x_path); orders=[]
    for b in bs:
        d=b.data
        if b.type_id==4 and len(d)>=12:
            fleet=(_u16(d,0)&0x1ff)+1; wp=_u16(d,2); x=_u16(d,4); y=_u16(d,6); target=_u16(d,8); warp=d[10]>>4; task=d[10]&15; obj=d[11]
            meta=coords.get(target)
            orders.append({'type':'WaypointAdd','fleet_display_id':fleet,'waypoint_index':wp,'x':x,'y':y,'target_planet_id':target,
                           'target_planet_display_id':target+1,'target_name':meta.name if meta else None,'warp':warp,'task':task,'object_type':obj})
        elif b.type_id==5 and len(d)>=12:
            fleet=(_u16(d,0)&0x1ff)+1; wp=_u16(d,2); x=_u16(d,4); y=_u16(d,6); target=_u16(d,8); warp=d[10]>>4; task=d[10]&15; obj=d[11]
            meta=coords.get(target)
            orders.append({'type':'WaypointChangeTask','fleet_display_id':fleet,'waypoint_index':wp,'x':x,'y':y,'target_planet_id':target,
                           'target_planet_display_id':target+1,'target_name':meta.name if meta else None,'warp':warp,'task':task,'object_type':obj})
        elif b.type_id==29:
            planet=_u16(d,0); items=[]
            # queue items are 4-byte entries after planet number; V1 preserves raw fields and recognizes standard IDs 7/8.
            for i in range(2,len(d),4):
                q=d[i:i+4]
                if len(q)<4: break
                chunk1=_u16(q,0); chunk2=_u16(q,2)
                item_id=chunk1 >> 10; count=chunk1 & 0x3ff
                complete_percent=chunk2 >> 4; item_type=chunk2 & 0x0f
                item_names={0:'Mines (Auto Build)',1:'Factories (Auto Build)',2:'Defenses (Auto Build)',
                            3:'Alchemy (Auto Build)',4:'Min Terraform (Auto Build)',5:'Max Terraform (Auto Build)',
                            6:'Mineral Packets (Auto Build)',7:'Factory',8:'Mine',9:'Defenses',11:'Mineral Alchemy',
                            14:'Ironium Mineral Packet',15:'Boranium Mineral Packet',16:'Germanium Mineral Packet',
                            17:'Mixed Mineral Packet',27:'Planetary Scanner'}
                if item_type==4 and 0<=item_id<16:
                    item_name=f'DesignSlot#{item_id}'
                elif item_type==4 and 16<=item_id<26:
                    item_name=f'StarbaseDesignSlot#{item_id-16}'
                else:
                    item_name=item_names.get(item_id,f'Item#{item_id}')
                items.append({'item_id':item_id,'item':item_name,'count':count,
                              'complete_percent':complete_percent,'item_type':item_type,'raw':q.hex()})
            meta=coords.get(planet)
            orders.append({'type':'ProductionQueueChange','planet_id':planet,'planet_display_id':planet+1,'planet_name':meta.name if meta else None,'items':items,'raw':d.hex()})
        elif b.type_id==35:
            planet=_u16(d,0); meta=coords.get(planet)
            orders.append({'type':'PlanetChange','planet_id':planet,'planet_display_id':planet+1,'planet_name':meta.name if meta else None,'raw':d.hex()})
        elif b.type_id==46: orders.append({'type':'SaveAndSubmit','raw':d.hex()})
    return {'header':header_dict(h),'orders':orders}


def header_dict(h:FileHeader)->dict:
    return {'game_id':h.game_id,'turn':h.turn,'year':h.year,'player_index':h.player_index,'player_number':h.player_index+1,
            'file_type':h.file_type,'turn_submitted':h.turn_submitted,'host_using':h.host_using,
            'multiple_turns':h.multiple_turns,'game_over':h.game_over,'unknown_bits':h.unknown_bits,
            'salt':h.salt,'flags':h.flags,'shareware':h.shareware}
