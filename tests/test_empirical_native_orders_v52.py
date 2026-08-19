
from types import SimpleNamespace
from stars_ai.native.x_writer import NativeBlock, _set_filehash_order_length, _waypoint_add_block

def _hash():
    return NativeBlock(9,17,bytes.fromhex("06 00 4f 91 6d 00 f3 00 00 00 00 f0 61 08 00 c0 aa"))

def _submit():
    return NativeBlock(46,4,bytes.fromhex("01 01 05 19"))

def test_filehash_length_one_movement_matches_real_stars():
    move=NativeBlock(4,12,bytes(12))
    out=_set_filehash_order_length([_hash()],[move,_submit()])
    assert out[0].data[:2] == bytes.fromhex("14 00")

def test_filehash_length_two_movements_matches_real_stars():
    move=NativeBlock(4,12,bytes(12))
    out=_set_filehash_order_length([_hash()],[move,move,_submit()])
    assert out[0].data[:2] == bytes.fromhex("22 00")

def test_filehash_length_research_matches_real_stars():
    research=NativeBlock(34,2,bytes.fromhex("0f 64"))
    out=_set_filehash_order_length([_hash()],[research,_submit()])
    assert out[0].data[:2] == bytes.fromhex("0a 00")

def test_fleet_owner_bits_match_four_player_samples():
    planet=SimpleNamespace(id=11,position=SimpleNamespace(x=1091,y=1598))
    for player_id, expected in [(1,0x0000),(2,0x0200),(3,0x0400),(4,0x0600)]:
        state=SimpleNamespace(player_id=player_id,planets=[planet])
        b=_waypoint_add_block(state,{"fleet_id":0,"destination_planet_id":11,"warp":6,"mission":"scan"})
        assert int.from_bytes(b.data[:2],"little") == expected

def test_player2_fleet4_matches_real_sample():
    planet=SimpleNamespace(id=42,position=SimpleNamespace(x=1304,y=1042))
    state=SimpleNamespace(player_id=2,planets=[planet])
    b=_waypoint_add_block(state,{"fleet_id":4,"destination_planet_id":42,"warp":8,"mission":"scan"})
    assert b.data[:11] == bytes.fromhex("04 02 01 00 18 05 12 04 2a 00 80")
