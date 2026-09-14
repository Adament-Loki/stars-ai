
from pathlib import Path
from stars_ai.adapters.stars_native import NativeBlock, read_blocks, parse_file_header
from stars_ai.models import GameState, Planet, Position, RaceProfile, Tech
from stars_ai.native.x_writer import (
    _encode_blocks,
    _updated_x_header,
    _production_block,
    _native_planet_mutation_authority,
    _current_colony_target_habitability,
    _pending_waypoint_delete_blocks,
    _pending_waypoint_delete_indices,
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


def test_pending_waypoint_deletion_uses_only_valid_pending_indices():
    # Index zero is the current waypoint, not a removable route leg.  Deleting
    # in descending order prevents Type-3 renumbering from changing the next
    # requested index.
    assert _pending_waypoint_delete_indices(0)==[]
    assert _pending_waypoint_delete_indices(1)==[]
    assert _pending_waypoint_delete_indices(2)==[1]
    assert _pending_waypoint_delete_indices(3)==[2,1]


def test_pending_waypoint_deletion_encodes_only_pending_slots():
    state=GameState("g",2401,1,RaceProfile(),Tech(),[],[])

    indices,blocks=_pending_waypoint_delete_blocks(
        state,fleet_id=4,waypoint_count=2
    )
    assert indices==[1]
    assert [block.data for block in blocks]==[bytes.fromhex("04 00 01 00")]

    indices,blocks=_pending_waypoint_delete_blocks(
        state,fleet_id=4,waypoint_count=3
    )
    assert indices==[2,1]
    assert [block.data for block in blocks]==[
        bytes.fromhex("04 00 02 00"),
        bytes.fromhex("04 00 01 00"),
    ]


def test_pending_waypoint_deletion_rejects_unencodable_native_count():
    import pytest

    with pytest.raises(ValueError, match="Type-3 index range"):
        _pending_waypoint_delete_indices(257)


def test_writer_rechecks_current_colony_target_habitability():
    target=Planet(17,"Red",Position(40,0),owner=None,observed=True,habitability=-7)
    state=GameState("g",2432,1,RaceProfile(),Tech(),[target],[])

    assert _current_colony_target_habitability(state,17)==-7
    assert _current_colony_target_habitability(state,99) is None


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
