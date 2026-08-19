
from stars_ai.adapters.stars_native import NativeBlock
from stars_ai.native.x_writer import _fresh_filehash_block

def test_game5_manual_submit_transaction_shape():
    # Controlled manual GAME(5).x2:
    # FileHash = 0x0034 (52)
    # gameplay orders = Type4/12, Type29/14, Type34/2
    # followed by THREE identical Type46/4 blocks.
    gameplay=[
        NativeBlock(4,12,b"x"*12),
        NativeBlock(29,14,b"y"*14),
        NativeBlock(34,2,b"z"*2),
    ]
    submit=NativeBlock(46,4,bytes.fromhex("01 01 05 19"))
    stream=gameplay+[submit,submit,submit]
    assert [b.type_id for b in stream]==[4,29,34,46,46,46]
    assert int.from_bytes(_fresh_filehash_block(stream).data[:2],"little")==0x34

def test_submit_payload_matches_controlled_manual_sample():
    assert bytes.fromhex("01 01 05 19")==bytes([1,1,5,25])
