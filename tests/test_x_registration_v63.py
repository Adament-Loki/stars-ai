
from stars_ai.native.x_writer import _updated_x_header,_fresh_filehash_block
from stars_ai.adapters.stars_native import NativeBlock,parse_file_header

def header(turn,player,file_type,flags,salt=111):
    b=bytearray(16)
    b[:4]=b"J3J3"
    b[4:8]=(0x12345678).to_bytes(4,"little")
    b[8:10]=(10848).to_bytes(2,"little")
    b[10:12]=turn.to_bytes(2,"little")
    b[12:14]=((salt<<5)|player).to_bytes(2,"little")
    b[14]=file_type
    b[15]=flags
    return bytes(b)

def test_current_m_turn_drives_generated_x_header():
    template=header(0,0,1,0xA1,444)
    current_m=header(7,0,3,0xA0,999)
    h=parse_file_header(_updated_x_header(template,current_m,1,salt=1234))
    assert h.turn==7
    assert h.salt==1234
    assert h.salt not in (444,999)
    assert h.player_index==0
    assert h.file_type==1
    assert h.turn_submitted
    assert not h.host_using
    assert not h.multiple_turns
    assert not h.game_over
    assert h.unknown_bits==5

def test_stale_x_low_flags_are_not_reused():
    template=header(0,0,1,0xAF,444)
    current_m=header(3,0,3,0x20,999)
    h=parse_file_header(_updated_x_header(template,current_m,1))
    assert h.flags==0x21

def test_fresh_filehash_matches_actual_serialized_orders():
    orders=[NativeBlock(4,12,b"x"*12),NativeBlock(34,2,b"y"*2)]
    fh=_fresh_filehash_block(orders)
    assert len(fh.data)==17
    assert int.from_bytes(fh.data[:2],"little")==18
    assert fh.data[2:].hex(" ")=="4f 91 6d 00 f3 00 00 00 00 f0 61 08 00 c0 aa"
