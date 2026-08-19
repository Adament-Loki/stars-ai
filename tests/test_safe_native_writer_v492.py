
from pathlib import Path
import stars_ai.native.x_writer as xw

def test_writer_does_not_invent_unknown_save_and_submit_payload():
    src=Path(xw.__file__).read_text(encoding="utf-8")
    assert 'submit=NativeBlock(46,4,bytes.fromhex("01 01 05 19"))' not in src
    assert "template_submit" in src
    assert "template_submit.data" in src
    assert "NativeBlock(46,submit.size,submit.data)" in src
    assert "order_stream.extend([" in src
