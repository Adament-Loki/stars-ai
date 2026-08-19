
from stars_ai.native.x_writer import _updated_x_header,_fresh_x_salt,_fresh_filehash_block
from stars_ai.adapters.stars_native import NativeBlock,parse_file_header

def _header(*,salt,player=2,file_type=3,flags=0,turn=1):
    b=bytearray(16)
    b[0:4]=b"J3J3"
    b[4:8]=(0x12345678).to_bytes(4,"little")
    b[8:10]=(0x2A60).to_bytes(2,"little")
    b[10:12]=turn.to_bytes(2,"little")
    b[12:14]=((salt<<5)|((player-1)&0x1f)).to_bytes(2,"little")
    b[14]=file_type
    b[15]=flags
    return bytes(b)

def test_explicit_fresh_salt_drives_x_header():
    template=_header(salt=235,file_type=1,flags=1)
    current=_header(salt=1048,file_type=3,flags=0)
    h=parse_file_header(_updated_x_header(template,current,2,salt=1288))
    assert h.salt==1288
    assert h.player_index==1
    assert h.file_type==1
    assert h.turn_submitted

def test_generated_salt_avoids_template_and_current_m_salts():
    for _ in range(16):
        s=_fresh_x_salt(avoid={235,1048})
        assert 0 <= s <= 2047
        assert s not in {235,1048}

def test_three_submit_blocks_match_controlled_manual_filehash_length():
    # GAME(5).x2 exact gameplay shape:
    # Type4 len12 => 14 serialized
    # Type29 len14 => 16 serialized
    # Type34 len2 => 4 serialized
    # 3x Type46 len4 => 18 serialized
    # total = 52 = 0x34, exactly as observed in the manual Stars! X.
    gameplay=[
        NativeBlock(4,12,b"A"*12),
        NativeBlock(29,14,b"B"*14),
        NativeBlock(34,2,b"C"*2),
    ]
    submit=NativeBlock(46,4,bytes.fromhex("01 01 05 19"))
    fh=_fresh_filehash_block(gameplay+[submit,submit,submit])
    assert int.from_bytes(fh.data[:2],"little")==52
