
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import secrets
from typing import Any
import json
import shutil

from stars_ai.adapters.stars_native import (
    read_blocks, parse_xy, NativeBlock, _rng, _crypt
)
from stars_ai.adapters.native_core_adapter import NativeCoreTurnAdapter
from stars_ai.agent import StarsAgent
from stars_ai.memory import AgentMemory
from stars_ai.persona import (
    BalancedPersona, ExpansionistPersona, IndustrialistPersona,
    TechnologistPersona, MilitaristPersona
)
from stars_ai.native_capabilities import capability
from stars_ai.planet_economy import theoretical_max_population, planet_population_capacity, population_capacity_fraction, projected_population_growth, projected_next_population

ORDER_BLOCK_TYPES = {
    1,2,3,4,5,10,19,23,24,25,27,29,34,35,36,37,38,40,42,44,46
}

@dataclass
class NativeWriteResult:
    player_id: int
    year: int
    emitted: list[dict]
    skipped: list[dict]
    output_x: str

def _u16(v:int)->bytes:
    return int(v).to_bytes(2,"little",signed=False)

def _block_header(type_id:int, size:int)->bytes:
    if size > 0x3ff:
        raise ValueError("Stars! block payload exceeds 10-bit block length")
    return ((type_id << 10) | size).to_bytes(2,"little")

def _encode_blocks(header_block: NativeBlock, blocks: list[NativeBlock]) -> bytes:
    """
    Encode a complete Stars! file using the header's RNG parameters.
    FileHeader is plaintext; every following block payload is encrypted while
    block headers remain plaintext.
    """
    if header_block.type_id != 8:
        raise ValueError("First block must be FileHeader")
    # Reparse header via a temporary in-memory-like direct reconstruction.
    from stars_ai.adapters.stars_native import parse_file_header
    header = parse_file_header(header_block.data)
    rng = _rng(header)
    out = bytearray()
    out += _block_header(8, len(header_block.data))
    out += header_block.data
    for b in blocks:
        out += _block_header(b.type_id, len(b.data))
        out += _crypt(b.data, rng)
    return bytes(out)


def _fresh_x_salt(*, avoid: set[int] | None = None) -> int:
    """Generate a fresh legal 11-bit Stars! encryption salt."""
    avoid=set(avoid or ())
    for _ in range(32):
        salt=secrets.randbelow(2048)
        if salt not in avoid:
            return salt
    # Vanishingly unlikely fallback that still guarantees a legal different salt.
    for salt in range(2048):
        if salt not in avoid:
            return salt
    raise RuntimeError("No legal Stars! encryption salt available")


def _updated_x_header(template_header: bytes, m_header: bytes, player_id:int, *, salt:int|None=None) -> bytes:
    """
    Build a submitted X header for the CURRENT M turn.

    v6.8.1 no longer treats the bootstrap X salt as permanent. Controlled native
    Save+Submit files show Stars! generates a new encryption salt for a new X.
    The salt drives encryption; it is not copied as static authentication state.
    """
    if len(template_header)!=16 or len(m_header)!=16:
        raise ValueError("Expected 16-byte FileHeader payload")
    out=bytearray(m_header)
    out[0:4]=b"J3J3"

    template_salt=int.from_bytes(template_header[12:14],"little") >> 5
    m_salt=int.from_bytes(m_header[12:14],"little") >> 5
    if salt is None:
        salt=_fresh_x_salt(avoid={template_salt,m_salt})
    salt=int(salt)
    if not (0 <= salt <= 0x7ff):
        raise ValueError(f"Stars! X salt must fit 11 bits; got {salt}")
    out[12:14]=((salt<<5)|((player_id-1)&0x1f)).to_bytes(2,"little")

    out[14]=1
    out[15]=(m_header[15] & 0xF0) | 0x01
    return bytes(out)


def _encode_queue_item(item_name:str, quantity:int, design_slot:int|None=None)->bytes:
    qty=max(0,min(1023,int(quantity)))
    if item_name=='ship_design':
        if design_slot is None or not (0<=int(design_slot)<=15): raise ValueError(f'Invalid ship design slot: {design_slot}')
        return _u16((int(design_slot)<<10)|qty)+_u16(4)
    ids={'factory':7,'mine':8,'defense':9}
    if item_name not in ids: raise ValueError(f'Unsupported validated production item: {item_name}')
    return _u16((ids[item_name]<<10)|qty)+_u16(2)


def _production_block(planet_id:int, queue:list[dict])->NativeBlock:
    data=bytearray(_u16(planet_id & 0x7ff))
    for q in queue:
        data += _encode_queue_item(str(q['item']),int(q['quantity']),int(q['design_slot']) if q.get('design_slot') is not None else None)
    return NativeBlock(29,len(data),bytes(data))


RESEARCH_FIELD_CODES = {
    "energy": 0x60,
    "weapons": 0x61,
    "propulsion": 0x62,
    "construction": 0x63,
    "electronics": 0x64,
    "biotechnology": 0x65,
}

def _research_change_block(field: str, allocation_percent: int = 100) -> NativeBlock:
    """
    Empirical Stars! ResearchChange encoding.

    Controlled samples:
      Electronics    -> 0F 64
      Biotechnology -> 0F 65

    The second byte maps cleanly to field codes 0x60..0x65.
    The first byte 0x0F is empirically observed for a normal 100% field switch,
    but its complete bit semantics are not yet decoded.
    """
    key=str(field).lower()
    if key not in RESEARCH_FIELD_CODES:
        raise ValueError(
            f"Unknown Stars! research field {field!r}. "
            "Supported fields: energy, weapons, propulsion, construction, electronics, biotechnology."
        )
    if int(allocation_percent) != 100:
        raise ValueError(
            "Integrated writer currently supports only the empirically validated "
            "100% research allocation form."
        )
    data=bytes([0x0F, RESEARCH_FIELD_CODES[key]])
    return NativeBlock(34,len(data),data)

def _waypoint_add_block(state:Any, payload:dict, object_type:int=0x11)->NativeBlock:
    fleet_id=int(payload["fleet_id"])
    target_id=int(payload["destination_planet_id"])
    target=next((p for p in state.planets if p.id==target_id),None)
    if target is None:
        raise ValueError(f"Unknown destination planet {target_id}")
    warp=max(0,min(15,int(payload.get("warp",7))))
    mission=str(payload.get("mission","")).lower()
    task=0
    object_type=int(object_type) & 0xff  # low nibble 1 = planet; preserve observed upper bits when known
    waypoint_index=1
    # Real Stars! four-player samples preserve owner/player bits in the raw
    # fleet number: P1=0x0000, P2=0x0200, P3=0x0400, P4=0x0600 for fleet 0.
    player_id=int(getattr(state,"player_id",1))
    raw_fleet=((player_id-1) << 9) | (fleet_id & 0x1ff)
    data=(
        _u16(raw_fleet)
        + _u16(waypoint_index)
        + _u16(int(target.position.x))
        + _u16(int(target.position.y))
        + _u16(target_id & 0x7ff)
        + bytes([(warp<<4)|task, object_type])
    )
    return NativeBlock(4,len(data),data)


def _raw_fleet_number(state:Any, fleet_id:int) -> int:
    player_id=int(getattr(state,"player_id",1))
    return ((player_id-1) << 9) | (int(fleet_id) & 0x1ff)

def _manual_load_population_25k_block(state:Any, fleet_id:int)->NativeBlock:
    return NativeBlock(1,7,_u16(_raw_fleet_number(state,fleet_id))+bytes.fromhex("25 00 12 08 19"))

def _manual_load_minerals_block(state:Any, fleet_id:int, load:dict)->NativeBlock:
    """
    Empirically generalized small mineral-load form.

    Controlled Stars!-generated samples:
      10/20/30 I/B/G -> prefix + 0A 14 1E
      20/20/20 I/B/G -> prefix + 14 14 14

    Because only those three bytes vary with the manually selected exact amounts,
    v6.8 treats them as unsigned one-byte Ironium/Boranium/Germanium quantities.
    """
    vals=[
        int(load.get("ironium",0) or 0),
        int(load.get("boranium",0) or 0),
        int(load.get("germanium",0) or 0),
    ]
    if any(v<0 or v>255 for v in vals):
        raise ValueError(
            f"Small native mineral load supports each I/B/G amount only in 0..255; got {vals}"
        )
    if sum(vals)<=0:
        raise ValueError("Refusing zero-size mineral load")

    fleet=_fleet_state(state,fleet_id)
    capacity=int(getattr(fleet,"cargo_capacity",0) or ((fleet.native or {}).get("cargo_capacity",0) if fleet else 0) or 0)
    current=0
    if fleet is not None:
        c=(fleet.native or {}).get("cargo",{})
        current=sum(int(c.get(k,0) or 0) for k in ("ironium","boranium","germanium","population"))
    if capacity>0 and current+sum(vals)>capacity:
        raise ValueError(
            f"Requested mineral load {sum(vals)}kT exceeds conservative available cargo capacity "
            f"{max(0,capacity-current)}kT for fleet {fleet_id}"
        )

    data=(
        _u16(_raw_fleet_number(state,fleet_id))
        +bytes.fromhex("25 00 12 07")
        +bytes(vals)
    )
    return NativeBlock(1,len(data),data)


def _manual_load_minerals_10_20_30_block(state:Any, fleet_id:int)->NativeBlock:
    """Compatibility wrapper retained for older tests."""
    return _manual_load_minerals_block(
        state,fleet_id,{"ironium":10,"boranium":20,"germanium":30}
    )

def _waypoint_change_task_block(state:Any, *, fleet_id:int, destination_planet_id:int,
                                warp:int, task:int, additional:bytes=b"", object_type:int=0x51)->NativeBlock:
    target=next((p for p in state.planets if p.id==int(destination_planet_id)),None)
    if target is None: raise ValueError(f"Unknown destination planet {destination_planet_id}")
    data=(
        _u16(_raw_fleet_number(state,fleet_id))+_u16(1)
        +_u16(int(target.position.x))+_u16(int(target.position.y))
        +_u16(int(destination_planet_id)&0x7ff)
        +bytes([((max(0,min(15,int(warp)))<<4)|(int(task)&0x0f)),object_type])
        +additional
    )
    return NativeBlock(5,len(data),data)


def _fleet_state(state:Any, fleet_id:int):
    return next(
        (f for f in state.fleets if f.owner==state.player_id and int(f.id)==int(fleet_id)),
        None,
    )


def _has_destination_waypoint_slot(state:Any, fleet_id:int)->bool:
    fleet=_fleet_state(state,fleet_id)
    if fleet is None:
        return False
    return int((fleet.native or {}).get("waypoint_count") or 0) >= 2


def _existing_waypoint_object_type(state:Any, fleet_id:int, fallback:int=0x11)->int:
    """Preserve Stars!' unknown upper target-type bits when replacing waypoint #1."""
    fleet=_fleet_state(state,fleet_id)
    if fleet is None:
        return fallback
    wps=(fleet.native or {}).get("waypoints") or []
    if len(wps) >= 2:
        raw=int(wps[1].get("position_object_type",fallback)) & 0xff
        if (raw & 0x0f) == 1:  # planet
            return raw
    return fallback


def _native_waypoint_action(state:Any, fleet_id:int)->str:
    return "CHANGE" if _has_destination_waypoint_slot(state,fleet_id) else "ADD"


def _movement_to_planet_block(
    state:Any,
    payload:dict,
    *,
    initial_object_type:int=0x11,
)->NativeBlock:
    """
    Multi-turn waypoint lifecycle.

    StarsAPI applies WaypointAdd as vector.add(index, waypoint), while a normal
    WaypointChangeTask replaces vector[index]. Once waypoint #1 already exists,
    another Type-4 Add is not a retarget operation. Replace waypoint #1 with a
    task-0 Type-5 block instead.
    """
    fid=int(payload["fleet_id"])
    if _has_destination_waypoint_slot(state,fid):
        object_type=_existing_waypoint_object_type(state,fid,initial_object_type)
        return _waypoint_change_task_block(
            state,
            fleet_id=fid,
            destination_planet_id=int(payload["destination_planet_id"]),
            warp=int(payload.get("warp",7)),
            task=0,
            object_type=object_type,
        )
    return _waypoint_add_block(state,payload,object_type=initial_object_type)

def _colonize_blocks(state:Any,payload:dict)->list[NativeBlock]:
    fid=int(payload["fleet_id"]); pid=int(payload["destination_planet_id"]); warp=int(payload.get("warp",7))
    out=[]
    if payload.get("load_25k_population"):
        out.append(_manual_load_population_25k_block(state,fid))
    route_type=_existing_waypoint_object_type(state,fid,0x51) if _has_destination_waypoint_slot(state,fid) else 0x51
    out.append(_movement_to_planet_block(
        state,{"fleet_id":fid,"destination_planet_id":pid,"warp":warp,"mission":"colonize"},
        initial_object_type=0x51,
    ))
    out.append(_waypoint_change_task_block(
        state,fleet_id=fid,destination_planet_id=pid,warp=warp,task=2,object_type=route_type
    ))
    return out


# Controlled Stars!-generated Transport waypoint policy:
#   Ironium    00 20 = Unload All
#   Boranium   00 20 = Unload All
#   Germanium  00 20 = Unload All
#   Population 00 20 = Unload All
#   Fuel       00 70 = Load Optimal
TRANSPORT_UNLOAD_ALL_LOAD_OPTIMAL = bytes.fromhex(
    "00 20 00 20 00 20 00 20 00 70"
)


def _transport_mineral_blocks(state:Any,payload:dict)->list[NativeBlock]:
    """Load at source, move, then let the destination Transport task unload/refuel."""
    fid=int(payload["fleet_id"])
    pid=int(payload["destination_planet_id"])
    warp=int(payload.get("warp",6))
    route_type=(
        _existing_waypoint_object_type(state,fid,0x51)
        if _has_destination_waypoint_slot(state,fid)
        else 0x51
    )
    return [
        _manual_load_minerals_block(state,fid,payload.get("load") or {"ironium":10,"boranium":20,"germanium":30}),
        _movement_to_planet_block(
            state,
            {"fleet_id":fid,"destination_planet_id":pid,"warp":warp,"mission":"transport"},
            initial_object_type=0x51,
        ),
        _waypoint_change_task_block(
            state,
            fleet_id=fid,
            destination_planet_id=pid,
            warp=warp,
            task=1,
            additional=TRANSPORT_UNLOAD_ALL_LOAD_OPTIMAL,
            object_type=route_type,
        ),
    ]




def _transport_unload_remainder_blocks(state:Any,payload:dict)->list[NativeBlock]:
    """
    Recovery only: if cargo is unexpectedly still aboard at an owned planet,
    reissue the complete validated Transport policy locally.
    """
    fid=int(payload["fleet_id"])
    pid=int(payload["destination_planet_id"])
    warp=max(1,min(9,int(payload.get("warp",1) or 1)))
    route_type=_existing_waypoint_object_type(state,fid,0x51)
    return [
        _waypoint_change_task_block(
            state,
            fleet_id=fid,
            destination_planet_id=pid,
            warp=warp,
            task=1,
            additional=TRANSPORT_UNLOAD_ALL_LOAD_OPTIMAL,
            object_type=route_type,
        )
    ]


RELATION_CODES = {
    "neutral": 0,
    "friend": 1,
    "enemy": 2,
}

def _player_relation_friend_block(target_player_id:int)->NativeBlock:
    """
    Controlled Stars!-generated sample:
      Player 2 marks Player 1 as Friend -> Type 38 payload: 01 00

    Payload layout used here:
      byte 0 = relation (1 = Friend)
      byte 1 = zero-based target player index

    Only Friend is enabled natively until Neutral/Enemy are separately sampled.
    """
    target=int(target_player_id)
    if target < 1 or target > 16:
        raise ValueError(f"Invalid Stars! target player id {target}")
    data=bytes([RELATION_CODES["friend"], (target-1) & 0xff])
    return NativeBlock(38,len(data),data)


FILEHASH_CANONICAL_TAIL = bytes.fromhex(
    "4f 91 6d 00 f3 00 00 00 00 f0 61 08 00 c0 aa"
)

def _fresh_filehash_block(order_stream:list[NativeBlock])->NativeBlock:
    order_len=sum(2+len(b.data) for b in order_stream if b.type_id != 0)
    data=_u16(order_len)+FILEHASH_CANONICAL_TAIL
    return NativeBlock(9,len(data),data)

def _set_filehash_order_length(blocks:list[NativeBlock], order_blocks:list[NativeBlock]) -> list[NativeBlock]:
    """Set FileHash bytes 0..1 to serialized order-stream length."""
    order_len=sum(2 + len(b.data) for b in order_blocks if b.type_id != 0)
    out=[]
    found=False
    for b in blocks:
        if b.type_id==9:
            if len(b.data) < 2:
                raise ValueError("FileHash block is shorter than 2 bytes")
            d=bytearray(b.data)
            d[0:2]=_u16(order_len)
            out.append(NativeBlock(9,len(d),bytes(d)))
            found=True
        else:
            out.append(b)
    if not found:
        raise RuntimeError("Known-good X template contains no FileHash block (type 9)")
    return out

def _static_template_blocks(template_blocks:list[NativeBlock])->list[NativeBlock]:
    """
    Preserve non-order metadata from a known-good X file (e.g. FileHash/Footer).
    SaveAndSubmit is regenerated at the end.
    """
    keep=[]
    for b in template_blocks:
        if b.type_id==8:
            continue
        if b.type_id in ORDER_BLOCK_TYPES:
            continue
        keep.append(NativeBlock(b.type_id,b.size,b.data))
    return keep

def _persona(name:str):
    n=(name or "Balanced").lower()
    if n=="expansionist": return ExpansionistPersona()
    if n=="industrialist": return IndustrialistPersona()
    if n=="technologist": return TechnologistPersona()
    if n=="militarist": return MilitaristPersona()
    return BalancedPersona()


def _object_name_for_planet(state, planet_id:int) -> str:
    p=next((p for p in state.planets if p.id==planet_id),None)
    return p.name if p is not None else f"Planet {planet_id}"

def _object_name_for_fleet(state, fleet_id:int) -> str:
    f=next((f for f in state.fleets if f.id==fleet_id and f.owner==state.player_id),None)
    return f.name if f is not None else f"Fleet {fleet_id}"

def _build_decision_report(state, orders, agent, emitted:list[dict], skipped:list[dict]) -> str:
    """
    Human-readable decision rationale. This is an inspectable summary of the
    selected actions and justifications, not private chain-of-thought.
    """
    lines=[
        f"STARS! AI DECISION REPORT — {state.game_name} — Year {state.year} — Player {state.player_id}",
        "",
        "Format: Object Name - Action - Reason/Justification",
        "",
        "FLEETS",
    ]

    by_fid={}
    for intent in getattr(agent,"fleet_intents",[]):
        by_fid[int(intent["fleet_id"])]=intent

    for fleet in [f for f in state.fleets if f.owner==state.player_id]:
        intent=by_fid.get(fleet.id,{
            "action":"BLOCKED",
            "reason":"Fleet missing from activity coverage.",
            "destination_planet_id":None,
        })
        action=intent["action"]
        dest=intent.get("destination_planet_id")
        if dest is not None:
            action=f"{action} -> {_object_name_for_planet(state,int(dest))}"
        lines.append(
            f"{fleet.name} - {action} - {intent['reason']}"
        )

    colony_intents=[
        x for x in getattr(agent,"fleet_intents",[])
        if x.get("role")=="colony"
    ]
    lines += ["", "COLONIZATION PRIORITY"]
    if colony_intents:
        for intent in colony_intents:
            fleet_name=intent.get("fleet_name",f"Fleet {intent.get('fleet_id')}")
            candidates=intent.get("colony_candidates",[])
            if candidates:
                lines.append(
                    f"{fleet_name} - BEST KNOWN CANDIDATE {candidates[0]['planet_name']} - "
                    f"{intent['reason']}"
                )
                for rank,c in enumerate(candidates,1):
                    lines.append(
                        f"  #{rank} {c['planet_name']} - score {c['score']:.1f} - "
                        f"habitability {c['habitability']}%; "
                        f"fleet distance {c['distance_from_fleet']:.1f}; "
                        f"nearest-owned distance {c['distance_from_nearest_owned']:.1f}; "
                        f"{c['explanation']}"
                    )
            else:
                lines.append(
                    f"{fleet_name} - NO COLONY TARGET - {intent.get('reason','No viable candidate')}"
                )
    else:
        lines.append(
            "Colonization - NO COLONY FLEET AVAILABLE - No owned fleet is currently classified as colony."
        )



    lines += ["", "DESIGN DEVELOPMENT"]
    design_orders=[o for o in orders.orders if o.kind=="create_design"]
    if design_orders:
        for o in design_orders:
            p=o.payload
            role=str(p.get("role","design")).upper()
            hull=p.get("desired_hull_name","unknown hull")
            engine=p.get("desired_engine")
            engine_text=f"; engine={engine}" if engine else ""
            objectives=", ".join(p.get("objectives",[]))
            lines.append(
                f"{p.get('name','New Design')} - PROPOSE {role} - hull={hull}{engine_text} - "
                f"{o.reason} Objectives: {objectives}. "
                "NATIVE STATUS: PENDING DESIGN-CREATION VALIDATION."
            )
    else:
        lines.append("Design Development - KEEP CURRENT DESIGNS - No material capability gap selected this turn.")

    lines += ["", "PLANET PRODUCTION"]
    production_orders=[o for o in orders.orders if o.kind=="set_planet_queue"]
    by_pid={int(o.payload["planet_id"]):o for o in production_orders}
    for p in [p for p in state.planets if p.owner==state.player_id]:
        o=by_pid.get(int(p.id))
        raw_pop=(p.native or {}).get("population_raw_hundreds","?")
        pop_year=(p.native or {}).get("population_source_year",state.year)
        sb=((p.native or {}).get("starbase_capabilities") or {})
        sb_text=(
            f"; base={sb.get('name')} shipyard={'Y' if sb.get('can_build_ships') else 'N'} "
            f"refuel={'Y' if sb.get('can_refuel') else 'N'}"
            if (p.native or {}).get("has_starbase") else ""
        )
    
        if o is None:
            pcap=planet_population_capacity(p,state.race)
            growth=projected_population_growth(p,state.race)
            next_pop=projected_next_population(p,state.race)
            cap_pct=population_capacity_fraction(p,state.race)*100.0
            lines.append(
                f"{p.name} - RESEARCH / KEEP EMPTY - "
                f"Y{pop_year} M-file population raw={raw_pop}x100 => {p.population:,}; "
                f"capacity={cap_pct:.1f}% of {pcap:,}; projected growth={growth:+,} -> {next_pop:,}; "
                f"factories {p.factories}; mines {p.mines}; Germanium={p.germanium}kT{sb_text}. "
                "No useful planet build selected this turn."
            )
            continue
    
        econ=o.payload.get("economy",{})
        cap_text=(
            f"Y{econ.get('population_source_year',pop_year)} M-file pop raw="
            f"{econ.get('population_raw_hundreds',raw_pop)}x100 => "
            f"{int(econ.get('population',p.population)):,}; "
            f"capacity {float(econ.get('population_capacity_percent',population_capacity_fraction(p,state.race)*100)):.1f}% "
            f"of {int(econ.get('planet_population_capacity',planet_population_capacity(p,state.race))):,}; "
            f"projected growth {int(econ.get('projected_growth',projected_population_growth(p,state.race))):+,} "
            f"-> {int(econ.get('projected_next_population',projected_next_population(p,state.race))):,}; "
            f"factories {int(econ.get('factories',p.factories))}/{int(econ.get('factory_cap',p.factories))}; "
            f"mines {int(econ.get('mines',p.mines))}/{int(econ.get('mine_cap',p.mines))}; "
            f"Germanium {int(econ.get('germanium_surface',p.germanium))}kT "
            f"(factory cost {int(econ.get('germanium_per_factory',4))}kT; "
            f"conc={econ.get('germanium_concentration','?')}){sb_text}"
        )
        queue=o.payload.get("queue",[])
        if queue:
            q=", ".join(
                (
                    f"{x.get('quantity',1)}x {x.get('design_name','Ship design')}"
                    if x.get('item')=='ship_design'
                    else f"{x.get('quantity',1)}x {x.get('item','?')}"
                )
                for x in queue
            )
            lines.append(f"{p.name} - BUILD {q} - {cap_text}. {o.reason}")
        else:
            lines.append(f"{p.name} - CLEAR QUEUE -> RESEARCH - {cap_text}. {o.reason}")
    
    lines += ["", "RESEARCH"]
    research=[o for o in orders.orders if o.kind=="set_research"]
    if research:
        for o in research:
            field=o.payload.get("field","unknown")
            pct=o.payload.get("allocation_percent",100)
            lines.append(
                f"Empire Research - {field.upper()} {pct}% - {o.reason}"
            )
    else:
        lines.append(
            "Empire Research - KEEP CURRENT FIELD - No research-field change selected this turn."
        )

    lines += ["", "DIPLOMACY"]
    relation_orders=[o for o in orders.orders if o.kind=="set_player_relation"]
    relation_emitted=[e for e in emitted if e.get("kind")=="set_player_relation"]
    if relation_orders:
        emitted_targets={int(e["payload"]["player_id"]) for e in relation_emitted}
        for o in relation_orders:
            target=int(o.payload["player_id"])
            relation=str(o.payload.get("relation","unknown")).upper()
            status="EMITTED" if target in emitted_targets else "NOT EMITTED"
            lines.append(f"Player {target} - {relation} - {status} - {o.reason}")
    else:
        lines.append("Diplomacy - NO RELATION CHANGE - Preserve current relations.")

    effective=list(state.race.native.get("player_relations",[]))
    actual=list(state.native.get("actual_player_relations",effective))
    for idx,rel in enumerate(effective):
        target=idx+1
        if target==state.player_id or int(rel)!=1:
            continue
        actual_rel=int(actual[idx]) if idx < len(actual) else 0
        if actual_rel==1:
            lines.append(f"Player {target} - FRIEND ACTIVE - Native relation is already Friend.")
        else:
            lines.append(f"Player {target} - FRIEND PENDING - Treated as Friend for decision safety; native Friend order is required this turn.")

    lines += ["", "NATIVE EXECUTION DETAILS"]
    for e in emitted:
        payload=e.get("payload",{})
        kind=e.get("kind")
        if kind in ("move_fleet","colony_operation","transport_minerals","transport_unload_remainder"):
            fid=int(payload.get("fleet_id",-1))
            name=_object_name_for_fleet(state,fid)
            target=payload.get("destination_planet_id")
            target_name=_object_name_for_planet(state,int(target)) if target is not None else "none"
            warp=payload.get("warp","?")
            if kind=="colony_operation":
                load="YES - 25k" if payload.get("load_25k_population") else "NO"
                lines.append(
                    f"{name} - NATIVE COLONY ORDER -> {target_name} warp {warp} - "
                    f"route={payload.get('native_waypoint_action','?')} waypoint #1; "
                    f"load block emitted: {load}; population aboard before={payload.get('cargo_population_before','?')}; "
                    f"source population={payload.get('source_population','?')}."
                )
            elif kind=="transport_minerals":
                lines.append(
                    f"{name} - NATIVE TRANSPORT ORDER -> {target_name} warp {warp} - "
                    f"load={payload.get('load')}; unload={payload.get('unload')}; "
                    f"Germanium pressure={payload.get('germanium_pressure',0)}."
                )
            elif kind=="transport_unload_remainder":
                lines.append(
                    f"{name} - NATIVE UNLOAD REMAINDER @ {target_name} - "
                    f"cargo before={payload.get('cargo_before')}; unload={payload.get('unload')}. "
                    f"{e.get('reason','')}"
                )
            else:
                fleet_state=_fleet_state(state,fid)
                wp_count=(fleet_state.native or {}).get("waypoint_count","?") if fleet_state is not None else "?"
                pos=(f"({int(fleet_state.position.x)},{int(fleet_state.position.y)})" if fleet_state is not None else "(?,?)")
                fp=payload.get('fuel_plan') or {}
                fuel_text=(f" fuel={fp.get('fuel','?')}/{fp.get('capacity','?')}mg; mass~{fp.get('mass','?')}kt; est burn={fp.get('estimated_fuel','?')}mg; engine={','.join(fp.get('engine_names',[])) or '?'}." if fp else "")
                lines.append(
                    f"{name} - NATIVE {payload.get('native_waypoint_action','?')} WAYPOINT -> {target_name} warp {warp} - "
                    f"from {pos}; M-file waypoint_count={wp_count}.{fuel_text} {e.get('reason','')}"
                )

    if skipped:
        lines += ["", "NATIVE WRITER LIMITATIONS / SKIPPED ACTIONS"]
        for s in skipped:
            payload=s.get("payload",{})
            if s.get("kind")=="move_fleet":
                name=_object_name_for_fleet(state,int(payload.get("fleet_id",-1)))
            elif s.get("kind")=="set_planet_queue":
                name=_object_name_for_planet(state,int(payload.get("planet_id",-1)))
            elif s.get("kind")=="set_research":
                name="Empire Research"
            else:
                name=s.get("kind","Order")
            lines.append(
                f"{name} - SKIPPED {s.get('kind','order')} - {s.get('reason','unknown reason')}"
            )

    unintentional=[
        x for x in getattr(agent,"fleet_intents",[])
        if x.get("action")=="BLOCKED"
    ]
    lines += ["", "COLONIZE NATIVE STATUS"]
    colony_ops=[x for x in getattr(agent,"fleet_intents",[]) if x.get("action")=="LOAD + COLONIZE"]
    colony_other=[x for x in getattr(agent,"fleet_intents",[]) if x.get("role")=="colony" and x.get("action")!="LOAD + COLONIZE"]
    if colony_ops:
        lines.append("Native Colonize task - EMITTED - Complete validated load/move/task=2 colony sequence generated.")
    elif colony_other:
        lines.append("Native Colonize task - DEFERRED - Colony asset is intentionally holding or returning for population; no purposeless colony movement emitted.")
    else:
        lines.append("Native Colonize task - NOT REQUIRED - No colony fleet available.")

    lines += ["", "ACTIVITY CHECK"]
    if unintentional:
        lines.append(
            f"WARNING - {len(unintentional)} fleet(s) BLOCKED - These fleets require capability/state follow-up; none are silently idle."
        )
    else:
        lines.append(
            "PASS - ALL OWNED FLEETS ACCOUNTED FOR - Every fleet has movement, an existing waypoint, or a conscious armed hold."
        )
    return "\n".join(lines) + "\n"

def write_ai_turn(
    *,
    player_id:int,
    m_path:Path,
    xy_path:Path,
    template_x_path:Path,
    output_x_path:Path,
    persona_name:str="Balanced",
    trace_path:Path|None=None,
    friend_player_ids:list[int]|None=None,
    memory_path:Path|None=None,
) -> NativeWriteResult:
    if not template_x_path.exists():
        raise FileNotFoundError(
            f"Missing X template {template_x_path}. A known-good .x{player_id} "
            "from this game is required to preserve X-file authentication/static blocks."
        )

    # Build GameState and semantic AI orders.
    adapter=NativeCoreTurnAdapter(xy_path=xy_path)
    state=adapter.read_state(m_path,player_id)

    requested_friends=sorted({
        int(x) for x in (friend_player_ids or [])
        if int(x) != int(player_id)
    })
    actual_relations=list(state.race.native.get("player_relations",[]))
    state.native["actual_player_relations"]=list(actual_relations)

    # Treat configured allies as Friends immediately for strategic/military
    # decisions so this turn cannot contain both "become Friend" and an attack
    # against that same player.
    effective_relations=list(actual_relations)
    if requested_friends:
        max_target=max(requested_friends)
        if len(effective_relations) < max_target:
            effective_relations.extend([0] * (max_target-len(effective_relations)))
        for target in requested_friends:
            effective_relations[target-1]=1
        state.race.native["player_relations"]=effective_relations

    memory=AgentMemory.load(memory_path)
    agent=StarsAgent(state,memory=memory,persona=_persona(persona_name))
    orders=agent.play_turn()

    # Emit the actual relation change only until the real M-file reports Friend.
    for target in requested_friends:
        actual = actual_relations[target-1] if 0 <= target-1 < len(actual_relations) else 0
        if int(actual) != 1:
            orders.add(
                "set_player_relation",
                {"player_id":target,"relation":"friend","configured_alliance":True},
                f"Configured reciprocal alliance: mark Player {target} as Friend.",
                priority=150,
            )
    orders.orders.sort(key=lambda o:o.priority, reverse=True)

    # Read current M header and known-good X template.
    _, mblocks, _ = read_blocks(m_path)
    m_header_block=next(b for b in mblocks if b.type_id==8)
    _, xblocks, _ = read_blocks(template_x_path)
    x_header_block=next(b for b in xblocks if b.type_id==8)

    template_salt=int.from_bytes(x_header_block.data[12:14],"little") >> 5
    m_salt=int.from_bytes(m_header_block.data[12:14],"little") >> 5
    fresh_salt=_fresh_x_salt(avoid={template_salt,m_salt})
    new_header=NativeBlock(
        8,16,_updated_x_header(
            x_header_block.data,m_header_block.data,player_id,salt=fresh_salt
        )
    )

    emitted=[]
    skipped=[]
    generated=[]
    touched_fleets=set()
    touched_planets=set()
    touched_relations=set()

    for o in orders.orders:
        cap=capability(o.kind)
        try:
            if o.kind=="colony_operation":
                fid=int(o.payload["fleet_id"])
                if fid in touched_fleets:
                    skipped.append({"kind":o.kind,"reason":"Fleet already has a higher-priority native operation.","payload":o.payload})
                    continue
                native_action=_native_waypoint_action(state,fid)
                generated.extend(_colonize_blocks(state,o.payload))
                touched_fleets.add(fid)
                ep=dict(o.payload); ep["native_waypoint_action"]=native_action
                emitted.append({"kind":o.kind,"payload":ep,"reason":o.reason})
            elif o.kind=="transport_unload_remainder":
                fid=int(o.payload["fleet_id"])
                if fid in touched_fleets:
                    skipped.append({"kind":o.kind,"reason":"Fleet already has a higher-priority native operation.","payload":o.payload})
                    continue
                generated.extend(_transport_unload_remainder_blocks(state,o.payload))
                touched_fleets.add(fid)
                emitted.append({"kind":o.kind,"payload":dict(o.payload),"reason":o.reason})
            elif o.kind=="transport_minerals":
                fid=int(o.payload["fleet_id"])
                if fid in touched_fleets:
                    skipped.append({"kind":o.kind,"reason":"Fleet already has a higher-priority native operation.","payload":o.payload})
                    continue
                native_action=_native_waypoint_action(state,fid)
                generated.extend(_transport_mineral_blocks(state,o.payload))
                touched_fleets.add(fid)
                ep=dict(o.payload); ep["native_waypoint_action"]=native_action
                emitted.append({"kind":o.kind,"payload":ep,"reason":o.reason})
            elif o.kind=="move_fleet":
                fid=int(o.payload["fleet_id"])
                if o.payload.get("deconflicted_hold"):
                    skipped.append({"kind":o.kind,"reason":"Recon duplicate had no unique fuel-safe target; hold instead of duplicating scan.","payload":o.payload})
                    continue
                if fid in touched_fleets:
                    skipped.append({"kind":o.kind,"reason":"Only highest-priority movement per fleet emitted.","payload":o.payload})
                    continue
                native_action=_native_waypoint_action(state,fid)
                generated.append(_movement_to_planet_block(state,o.payload,initial_object_type=0x11))
                touched_fleets.add(fid)
                ep=dict(o.payload); ep["native_waypoint_action"]=native_action
                emitted.append({"kind":o.kind,"payload":ep,"reason":o.reason})
            elif o.kind=="set_planet_queue":
                pid=int(o.payload["planet_id"])
                if pid in touched_planets:
                    continue
                supported=[
                    q for q in o.payload.get('queue',[])
                    if q.get('item') in ('factory','mine','defense','ship_design')
                ]
                clear_queue=bool(o.payload.get("clear_queue",False))
                if not supported and not clear_queue:
                    skipped.append({"kind":o.kind,"reason":"No validated queue items and clear_queue was not requested.","payload":o.payload})
                    continue
                # StarsAPI ProductionQueueChange supports a zero-item queue: the
                # payload is then only the 2-byte planet id. This is used to clear
                # stale infrastructure queues once population operating caps are met.
                generated.append(_production_block(pid,supported))
                touched_planets.add(pid)
                emitted.append({
                    "kind":o.kind,
                    "payload":{
                        "planet_id":pid,
                        "queue":supported,
                        "clear_queue":clear_queue,
                        "research_when_idle":bool(o.payload.get("research_when_idle",False)),
                        "economy":o.payload.get("economy",{}),
                    },
                    "reason":o.reason,
                })
            elif o.kind=="set_research":
                requested=str(o.payload["field"]).lower()
                generated.append(
                    _research_change_block(
                        requested,
                        int(o.payload.get("allocation_percent",100)),
                    )
                )
                emitted.append({
                    "kind":o.kind,
                    "payload":dict(o.payload),
                    "reason":o.reason,
                })
            elif o.kind=="set_player_relation":
                target=int(o.payload["player_id"])
                relation=str(o.payload.get("relation","")).lower()
                if target==player_id:
                    skipped.append({"kind":o.kind,"reason":"Cannot set relation toward self.","payload":o.payload})
                    continue
                if target in touched_relations:
                    continue
                if relation!="friend":
                    skipped.append({
                        "kind":o.kind,
                        "reason":"PARTIAL: only empirically validated Friend relation serialization is enabled.",
                        "payload":o.payload,
                    })
                    continue
                generated.append(_player_relation_friend_block(target))
                touched_relations.add(target)
                emitted.append({"kind":o.kind,"payload":dict(o.payload),"reason":o.reason})
            else:
                skipped.append({
                    "kind":o.kind,
                    "reason":f"{cap.status}: {cap.reason}",
                    "payload":o.payload
                })
        except Exception as exc:
            skipped.append({
                "kind":o.kind,
                "reason":f"writer error: {type(exc).__name__}: {exc}",
                "payload":o.payload
            })

    # Build a fresh current-turn X transaction.
    # Valid controlled Stars! X files exist both with and without Type 46.
    # Save+Submit lifecycle: do not invent an unknown Type-46 payload, but when
    # the known-good template supplies one, reproduce the three consecutive blocks
    # observed in the latest controlled Stars!-generated Save+Submit X for this workflow.
    template_submit=next((b for b in xblocks if b.type_id==46),None)
    order_stream=list(generated)
    if template_submit is not None:
        submit=NativeBlock(46,template_submit.size,template_submit.data)
        order_stream.extend([
            submit,
            NativeBlock(46,submit.size,submit.data),
            NativeBlock(46,submit.size,submit.data),
        ])

    filehash=_fresh_filehash_block(order_stream)
    final_blocks=[filehash]+order_stream+[NativeBlock(0,0,b"")]

    raw=_encode_blocks(new_header,final_blocks)
    output_x_path.parent.mkdir(parents=True,exist_ok=True)
    output_x_path.write_bytes(raw)

    # Internal registration validation.
    check_header, check_blocks, _=read_blocks(output_x_path)
    if int(check_header.turn) != int(state.year-2400):
        raise RuntimeError(f"Generated X turn {check_header.turn} != current year {state.year}")
    if int(check_header.player_index) != int(player_id-1):
        raise RuntimeError("Generated X player index mismatch")
    if int(check_header.file_type) != 1:
        raise RuntimeError(f"Generated file type {check_header.file_type} is not X type 1")
    if not check_header.turn_submitted:
        raise RuntimeError("Generated X is not marked turnSubmitted")

    fh=next((b for b in check_blocks if b.type_id==9),None)
    if fh is None or len(fh.data)!=17:
        raise RuntimeError("Generated X missing canonical 17-byte FileHash")
    actual_order_len=sum(
        2+len(b.data)
        for b in check_blocks
        if b.type_id in ORDER_BLOCK_TYPES and b.type_id != 0
    )
    stored_order_len=int.from_bytes(fh.data[:2],"little")
    if stored_order_len != actual_order_len:
        raise RuntimeError(
            f"Generated X FileHash order length {stored_order_len} != actual {actual_order_len}"
        )

    # v7.0 persistent state is committed only after the generated X passes the
    # writer's internal registration/FileHash validation. Memory contains only
    # current observations and finalized intent history, so a host retry at the
    # same M turn remains safe.
    agent.memory.save(memory_path)

    result=NativeWriteResult(player_id,state.year,emitted,skipped,str(output_x_path))
    decision_report=_build_decision_report(state,orders,agent,emitted,skipped)
    if trace_path:
        trace_path.parent.mkdir(parents=True,exist_ok=True)
        trace_path.write_text(json.dumps({
            "state":{"game":state.game_name,"year":state.year,"player":state.player_id},
            "orders":orders.to_dict(),
            "fleet_intents":getattr(agent,"fleet_intents",[]),
            "x_lifecycle":{
                "fresh_salt":fresh_salt,
                "template_salt":template_salt,
                "m_salt":m_salt,
                "save_submit_count":sum(1 for b in order_stream if b.type_id==46),
                "filehash_order_length":int.from_bytes(filehash.data[:2],"little"),
            },
            "persistent_intel":state.native.get("persistent_intel",{}),
            "strategic_watchdog":state.native.get("strategic_watchdog",{}),
            "probe_route_diagnostics":state.native.get("probe_route_diagnostics",[]),
            "persistent_scout_routes":dict(getattr(agent.memory,"scout_routes",{})),
            "memory_path":str(memory_path) if memory_path else None,
            "native_result":asdict(result),
        },indent=2),encoding="utf-8")
        report_path=trace_path.with_name(
            trace_path.name.replace("-decision-native.json","-DECISION_REPORT.txt")
        )
        report_path.write_text(decision_report,encoding="utf-8")
    return result
