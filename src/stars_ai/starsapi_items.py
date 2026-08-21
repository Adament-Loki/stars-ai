"""Unified StarsAPI-compatible hull/component model for native ship design (v8.8).

The project previously had three independent interpretations of stock hulls:
``standard_hulls``, ``ship_design_synth`` and ``fuel_planner``.  v8.8 makes this
module the canonical source consumed by legality, synthesis and fuel/mass code.

Grounding:
* StarsAPI ``DesignBlock`` / ``Items`` category masks and mass/fuel rules.
* the canonical stock MOD hull rows bundled in ``data_hulls.mod``.
* runholen/stars ``slot-ids.txt`` for the actual DesignBlock slot geometry.

Important upstream discrepancy: StarsAPI's historical ``SLOT_COUNT_INDEX=48``
does not describe the actual DesignBlock slot count for several stock hull rows.
We therefore preserve that raw field for diagnostics but serialize using the
canonical stock slot geometry validated against slot-ids / native DesignBlocks.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable


# DesignBlock / Items.java category masks.
EMPTY=0x0000; ENGINE=0x0001; SCANNER=0x0002; SHIELD=0x0004; ARMOR=0x0008
BEAM=0x0010; TORPEDO=0x0020; BOMB=0x0040; MINING_ROBOT=0x0080
MINE_LAYER=0x0100; ORBITAL=0x0200; PLANETARY=0x0400; ELECTRICAL=0x0800; MECHANICAL=0x1000

CATEGORY_NAMES={EMPTY:"Empty",ENGINE:"Engine",SCANNER:"Scanner",SHIELD:"Shield",ARMOR:"Armor",BEAM:"Beam Weapon",TORPEDO:"Torpedo",BOMB:"Bomb",MINING_ROBOT:"Mining Robot",MINE_LAYER:"Mine Layer",ORBITAL:"Orbital",PLANETARY:"Planetary",ELECTRICAL:"Electrical",MECHANICAL:"Mechanical"}

# Canonical actual DesignBlock slot counts (runholen/stars slot-ids + native files).
ACTUAL_SLOT_COUNTS={
    0:3,1:3,2:3,3:4,4:3,5:4,6:7,7:7,8:7,9:11,10:13,11:5,12:9,13:8,
    14:2,15:2,16:2,17:4,18:5,19:7,20:2,21:4,22:6,23:6,24:6,25:2,26:3,
    27:4,28:6,29:13,30:7,31:7,32:5,33:8,34:12,35:16,36:16,
}

# Exact hull-level PRT restrictions from StarsAPI DesignBlock.getPrt().
# Stars! PRT IDs: HE=0, SS=1, WM=2, CA=3, IS=4, SD=5, PP=6, IT=7, AR=8, JOAT=9.
HULL_REQUIRED_PRT={3:4,8:2,10:2,12:1,14:0,18:1,25:4,27:5,28:5,31:0,36:8}
# LRT-only starbase hulls already established in the project.
HULL_REQUIRED_LRT={33:"ISB",35:"ISB"}

# Engine data historically validated by the project.  Kept here so fuel planning
# and design synthesis use the same component object rather than duplicate tables.
ENGINE_DATA={
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

# Component masses ported from the project's validated stock table, now centralized.
_COMPONENT_MASSES={
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
class SlotSpec:
    index:int
    allowed_categories:int
    capacity:int
    required:bool=False
    def allows(self, category:int)->bool:
        return int(category)==0 or bool(int(self.allowed_categories)&int(category))

@dataclass(frozen=True)
class HullSpec:
    hull_id:int; name:str; is_starbase:bool
    tech_required:tuple[int,int,int,int,int,int]
    mass:int; armor:int; cargo:int; fuel:int; pic:int; engine_count:int
    slots:tuple[SlotSpec,...]
    raw_starsapi_slot_count_field:int
    required_prt:int|None=None
    required_lrt:str|None=None
    @property
    def slot_count(self)->int: return len(self.slots)

@dataclass(frozen=True)
class ComponentSpec:
    category:int; item_id:int; name:str
    mass:int; mass_exact:bool=True
    fuel_bonus:int=0; armor_bonus:int=0
    tech_required:tuple[int,int,int,int,int,int]=(0,0,0,0,0,0)
    required_prt:int|None=None
    required_lrt:str|None=None
    def key(self)->tuple[int,int]: return (int(self.category),int(self.item_id))


def component_mass(category:int,item_id:int)->tuple[int,bool]:
    value=_COMPONENT_MASSES.get(int(category),{}).get(int(item_id))
    return (int(value),True) if value is not None else (int(CATEGORY_MAX_MASS.get(int(category),100)),False)


@lru_cache(maxsize=1)
def stock_component_database():
    """Return the bundled, unmodified StarsAPI stock component database.

    ``data_unedited.mod`` is a byte-for-byte copy of StarsAPI's
    ``UNEDITED.MOD``.  Unlike the smaller hull-only data file, it contains the
    research requirements and production metadata for every fit-able item.
    """
    # Delayed import avoids the standard-hull/legality import cycle during
    # native-state startup.
    from .standard_mod import parse_mod_file
    return parse_mod_file(Path(__file__).with_name("data_unedited.mod"))


# The official Stars! help describes the trait gates below; UNEDITED.MOD holds
# the item IDs and six research requirements.  Keep names here rather than
# opaque IDs so a future stock-MOD update fails visibly instead of silently
# assigning a special gate to the wrong component.
PRT_COMPONENT_NAMES={
    0:frozenset({"Settler's Delight","Flux Capacitor"}),
    1:frozenset({
        "Pick Pocket Scanner","Chameleon Scanner","Robber Baron Scanner",
        "Shadow Shield","Transport Cloaking","Stealth Cloak",
        "Super-Stealth Cloak","Ultra-Stealth Cloak",
    }),
    2:frozenset({"Gatling Neutrino Cannon","Blunderbuss"}),
    3:frozenset({
        "Retro Bomb","Orbital Adjuster","Smart Bomb","Neutron Bomb",
        "Enriched Neutron Bomb","Peerless Bomb","Annihilator Bomb",
    }),
    4:frozenset({
        "Croby Sharmor","Langston Shell","Mini Gun","Speed Trap 20",
        "Jammer 10","Jammer 50","Tachyon Detector",
    }),
    5:frozenset({
        # Standard Mine Dispensers are general components: any PRT may fit
        # them in a compatible general-purpose hull. Space Demolition alone
        # owns the dedicated heavy/speed-trap/energy-dampener equipment (and
        # the Mini/Super Mine Layer hulls in ``HULL_REQUIRED_PRT``).
        "Heavy Dispenser 50","Heavy Dispenser 110",
        "Heavy Dispenser 200","Speed Trap 20","Speed Trap 30","Speed Trap 50",
        "Energy Dampener",
    }),
    6:frozenset({
        "Mass Driver 5","Mass Driver 6","Mass Driver 7","Super Driver 8",
        "Super Driver 9","Ultra Driver 10","Ultra Driver 11",
        "Ultra Driver 12","Ultra Driver 13",
    }),
    7:frozenset({"Anti-matter Generator"}),
    8:frozenset({"Orbital Construction Module"}),
}

LRT_REQUIRED_COMPONENT_NAMES={
    "IFE":frozenset({"Fuel Mizer","Galaxy Scoop"}),
    "NRSE":frozenset({"Interspace-10"}),
    # ARM's two robot-only additions are Robo-Midget and Robo-Ultra Miner.
    "ARM":frozenset({"Robo-Midget Miner","Robo-Ultra-Miner"}),
}

# The help file calls out the hard exclusions.  NAS removes standard
# penetrating scanners; the final MOD field distinguishes their scanner type.
LRT_FORBIDDEN_COMPONENT_NAMES={
    "NRSE":frozenset({
        "Radiating Hydro-Ram Scoop","Sub-Galactic Fuel Scoop",
        "Trans-Galactic Fuel Scoop","Trans-Galactic Super Scoop",
        "Trans-Galactic Mizer Scoop","Galaxy Scoop",
    }),
    "OBRM":frozenset({"Robo-Miner","Robo-Maxi-Miner","Robo-Super-Miner"}),
}


@lru_cache(maxsize=1)
def _component_trait_maps():
    db=stock_component_database()
    by_name={spec.name:(int(spec.category),int(spec.item_id)) for spec in db.components.values()}
    missing=set()
    for names in (*PRT_COMPONENT_NAMES.values(),*LRT_REQUIRED_COMPONENT_NAMES.values(),*LRT_FORBIDDEN_COMPONENT_NAMES.values()):
        missing.update(name for name in names if name not in by_name)
    if missing:
        raise RuntimeError(f"Stock UNEDITED.MOD is missing known trait-gated components: {sorted(missing)}")
    prt={by_name[name]:prt for prt,names in PRT_COMPONENT_NAMES.items() for name in names}
    required_lrt={by_name[name]:lrt for lrt,names in LRT_REQUIRED_COMPONENT_NAMES.items() for name in names}
    forbidden_lrt={by_name[name]:lrt for lrt,names in LRT_FORBIDDEN_COMPONENT_NAMES.items() for name in names}
    # ``scannerType`` is the last populated field in the stock scanner rows.
    # A non-zero type denotes a penetrating scanner, which NAS explicitly
    # removes.  PRT-only scanners are already guarded by their PRT gate.
    for key,spec in db.components.items():
        if int(spec.category)!=SCANNER:
            continue
        row=next((line for line in Path(__file__).with_name("data_unedited.mod").read_text(encoding="latin-1").splitlines() if f'"{spec.name}"' in line),"")
        parts=next(iter(csv.reader([row])),[])
        if len(parts)>17 and int(parts[17] or 0)>0:
            forbidden_lrt.setdefault((int(key[0]),int(key[1])),"NAS")
    # The stock planetary X-series (Snooper) scanners are likewise
    # planet-penetrating and unavailable to No Advanced Scanners races.  Their
    # range is encoded as a negative value in UNEDITED.MOD rather than the ship
    # scanner's ``scannerType`` field, so apply the official trait rule by their
    # canonical stock names.
    for key,spec in db.components.items():
        if int(spec.category)==PLANETARY and spec.name.startswith("Snooper "):
            forbidden_lrt.setdefault((int(key[0]),int(key[1])),"NAS")
    return prt,required_lrt,forbidden_lrt


def component_spec(category:int,item_id:int,name:str|None=None)->ComponentSpec:
    key=(int(category),int(item_id))
    mod=stock_component_database().component(*key)
    trait_prt,trait_lrt,_=_component_trait_maps()
    mass,exact=component_mass(category,item_id)
    if mod is not None:
        mass=int(mod.mass); exact=True
    fuel=0
    if int(category)==MECHANICAL and int(item_id)==5: fuel=250
    elif int(category)==MECHANICAL and int(item_id)==6: fuel=500
    elif int(category)==ELECTRICAL and int(item_id)==16: fuel=200
    return ComponentSpec(
        int(category),int(item_id),name or (mod.name if mod is not None else f"{CATEGORY_NAMES.get(int(category),'Item')} #{int(item_id)}"),
        mass,exact,fuel_bonus=fuel,
        tech_required=(mod.tech_required if mod is not None else (0,0,0,0,0,0)),
        required_prt=trait_prt.get(key),required_lrt=trait_lrt.get(key),
    )


def _raw(nums:list[int], idx:int)->int:
    return int(nums[idx]) if 0<=idx<len(nums) else 0

@lru_cache(maxsize=1)
def stock_hulls()->dict[int,HullSpec]:
    path=Path(__file__).with_name("data_hulls.mod")
    out={}
    for parts in csv.reader(path.read_text(encoding="latin-1").splitlines()):
        if len(parts)<4 or int(parts[0]) not in (15,16): continue
        cat=int(parts[0]); nums=[int(x) if x else 0 for x in parts[3:]]
        hid=int(nums[0]); is_starbase=cat==16; count=ACTUAL_SLOT_COUNTS[hid]
        slots=[]
        if not is_starbase:
            slots.append(SlotSpec(0,ENGINE,_raw(nums,17),True))
            for i in range(1,count):
                slots.append(SlotSpec(i,_raw(nums,16+2*i),_raw(nums,17+2*i),False))
        else:
            for i in range(count):
                slots.append(SlotSpec(i,_raw(nums,16+2*i),_raw(nums,17+2*i),False))
        out[hid]=HullSpec(
            hid,str(parts[2]),is_starbase,tuple(_raw(nums,i) for i in range(1,7)),
            _raw(nums,7),_raw(nums,15),_raw(nums,13),_raw(nums,14),_raw(nums,12),
            (0 if is_starbase else _raw(nums,17)),tuple(slots),_raw(nums,48),
            HULL_REQUIRED_PRT.get(hid),HULL_REQUIRED_LRT.get(hid),
        )
    if set(out)!=set(range(37)):
        raise RuntimeError(f"Stock hull model incomplete: got {sorted(out)}")
    return out


def prt_id(state:Any)->int|None:
    native=(getattr(getattr(state,"race",None),"native",{}) or {})
    if native.get("prt_id") is not None: return int(native["prt_id"])
    return None


def lrts(state:Any)->set[str]:
    return {str(x).upper() for x in (((getattr(getattr(state,"race",None),"native",{}) or {}).get("lrts",[])) or [])}


def hull_available_to_race(hull:HullSpec,state:Any)->bool:
    if hull.required_prt is not None and prt_id(state)!=int(hull.required_prt): return False
    if hull.required_lrt is not None and hull.required_lrt.upper() not in lrts(state): return False
    return True


def hull_unlocked(hull:HullSpec,state:Any)->bool:
    tech=getattr(state,"tech",None)
    have=tuple(int(getattr(tech,f,0) or 0) for f in ("energy","weapons","propulsion","construction","electronics","biotechnology"))
    return hull_available_to_race(hull,state) and all(h>=r for h,r in zip(have,hull.tech_required))


def iter_design_slots(state:Any)->Iterable[tuple[int,int,int]]:
    for d in (((getattr(state,"native",{}) or {}).get("designs",[])) or []):
        if bool(d.get("is_starbase")): continue
        for raw in d.get("slots",[]) or []:
            cat=int(raw.get("category",0) if isinstance(raw,dict) else getattr(raw,"category",0))
            item=int(raw.get("item_id",0) if isinstance(raw,dict) else getattr(raw,"item_id",0))
            count=int(raw.get("count",0) if isinstance(raw,dict) else getattr(raw,"count",0))
            if cat and count: yield cat,item,count


def researched_available_components(state:Any)->dict[tuple[int,int],ComponentSpec]:
    """Every stock component this race may legally fit *this turn*.

    Research levels are read from the current M file state, PRT/LRT gates are
    applied from the official Stars! help, and item identity/tech requirements
    come from the bundled StarsAPI ``UNEDITED.MOD``.  Existing designs are not
    used as a proxy for research availability.
    """
    from .standard_mod import TechLevels
    tech_obj=getattr(state,"tech",None)
    tech=TechLevels(**{field:int(getattr(tech_obj,field,0) or 0) for field in TechLevels.__dataclass_fields__})
    db=stock_component_database()
    available=db.available_components(tech)
    trait_prt,trait_lrt,forbidden_lrt=_component_trait_maps()
    race_prt=prt_id(state)
    race_lrts=lrts(state)
    out={}
    for key in sorted(available):
        needed_prt=trait_prt.get(key)
        needed_lrt=trait_lrt.get(key)
        denied_lrt=forbidden_lrt.get(key)
        if needed_prt is not None and race_prt!=needed_prt:
            continue
        if needed_lrt is not None and needed_lrt not in race_lrts:
            continue
        if denied_lrt is not None and denied_lrt in race_lrts:
            continue
        out[key]=component_spec(*key)
    return out


def proven_available_components(state:Any)->dict[tuple[int,int],ComponentSpec]:
    """Backward-compatible name for the full race-aware availability model."""
    return researched_available_components(state)


def design_mass_and_fuel(hull_id:int,slots:Iterable[Any])->tuple[int,int,bool]:
    hull=stock_hulls()[int(hull_id)]
    mass=0 if hull.is_starbase else int(hull.mass); fuel=0 if hull.is_starbase else int(hull.fuel); exact=True
    for raw in slots:
        cat=int(raw.get("category",0) if isinstance(raw,dict) else getattr(raw,"category",0))
        item=int(raw.get("item_id",0) if isinstance(raw,dict) else getattr(raw,"item_id",0))
        count=int(raw.get("count",0) if isinstance(raw,dict) else getattr(raw,"count",0))
        if count<=0: continue
        spec=component_spec(cat,item); mass+=spec.mass*count; fuel+=spec.fuel_bonus*count; exact=exact and spec.mass_exact
    return mass,fuel,exact
