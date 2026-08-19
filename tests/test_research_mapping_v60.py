
import pytest
from stars_ai.native.x_writer import _research_change_block

@pytest.mark.parametrize("field,hex_payload",[
    ("energy","0F 60"),
    ("weapons","0F 61"),
    ("propulsion","0F 62"),
    ("construction","0F 63"),
    ("electronics","0F 64"),
    ("biotechnology","0F 65"),
])
def test_research_payloads(field,hex_payload):
    assert _research_change_block(field,100).data==bytes.fromhex(hex_payload)

def test_unknown_field_rejected():
    with pytest.raises(ValueError):
        _research_change_block("alchemy",100)
