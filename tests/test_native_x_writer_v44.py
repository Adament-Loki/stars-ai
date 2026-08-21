
from pathlib import Path
from stars_ai.adapters.stars_native import NativeBlock, read_blocks, parse_file_header
from stars_ai.models import GameState, Planet, Position, RaceProfile, Tech
from stars_ai.native.x_writer import (
    _encode_blocks,
    _updated_x_header,
    _production_block,
    _native_planet_mutation_authority,
)

def header(file_type=1, turn=0, player=0, salt=8):
    d=bytearray(16)
    d[0:4]=b"J3J3"
    d[4:8]=(123456).to_bytes(4,"little")
    d[8:10]=(10848).to_bytes(2,"little")
    d[10:12]=turn.to_bytes(2,"little")
    d[12:14]=((salt<<5)|player).to_bytes(2,"little")
    d[14]=file_type
    d[15]=0
    return bytes(d)

def test_block_encrypt_roundtrip(tmp_path):
    h=NativeBlock(8,16,header())
    blocks=[NativeBlock(9,4,b"ABCD"),_production_block(3,[{"item":"factory","quantity":5}]),NativeBlock(46,0,b""),NativeBlock(0,0,b"")]
    p=tmp_path/"a.x1"
    p.write_bytes(_encode_blocks(h,blocks))
    hh,bb,_=read_blocks(p)
    assert hh.game_id==123456
    assert [b.type_id for b in bb]==[8,9,29,46,0]
    assert bb[2].data[0:2]==(3).to_bytes(2,"little")

def test_update_x_header_uses_current_m_turn_and_game():
    x=header(file_type=1,turn=2,player=0,salt=11)
    m=bytearray(header(file_type=3,turn=9,player=0,salt=7))
    m[4:8]=(999).to_bytes(4,"little")
    out=_updated_x_header(x,bytes(m),2)
    ph=parse_file_header(out)
    assert ph.turn==9
    assert ph.game_id==999
    assert ph.player_index==1
    assert out[14]==1


def test_current_m_ownership_allow_list_rejects_stale_planet_mutation():
    state=GameState(
        "g",2479,1,RaceProfile(),Tech(),[
            Planet(23,"Owned",Position(0,0),owner=1),
            Planet(
                131,"Lost",Position(1,0),owner=1,
                native={
                    "current_m_record":True,
                    "current_m_owner":None,
                    "intel_source":"current_m_unowned",
                    "native_planet_mutation_allowed":False,
                },
            ),
        ],[],
        native={"current_m_owned_planet_ids":[23]},
    )

    assert _native_planet_mutation_authority(state,23)[0] is True
    allowed,diagnostic=_native_planet_mutation_authority(state,131)
    assert allowed is False
    assert diagnostic["current_m_owner"] is None
    assert diagnostic["intel_source"]=="current_m_unowned"
