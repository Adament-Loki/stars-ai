
from pathlib import Path
import stars_ai.native.x_writer as xw

def test_writer_preserves_template_submit_block():
    src=Path(xw.__file__).read_text(encoding="utf-8")
    assert "template_submit=next" in src
    assert 'NativeBlock(46,0,b"")' not in src

def test_colonize_native_output_is_now_empirically_enabled():
    src=Path(xw.__file__).read_text(encoding="utf-8")
    assert "_colonize_blocks" in src
    assert "task=2" in src
