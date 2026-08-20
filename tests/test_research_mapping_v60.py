
import pytest
from stars_ai.native.x_writer import _research_change_block

@pytest.mark.parametrize("current,next_field,pct,hex_payload",[
    ("energy","construction",15,"0F 30"),
    ("energy","weapons",15,"0F 10"),
    ("construction","electronics",25,"19 43"),
    ("electronics","weapons",15,"0F 14"),
])
def test_research_payloads(current,next_field,pct,hex_payload):
    assert _research_change_block(current,pct,next_field).data==bytes.fromhex(hex_payload)

def test_unknown_field_rejected():
    with pytest.raises(ValueError):
        _research_change_block("alchemy",15,"energy")

def test_unvalidated_allocation_rejected():
    with pytest.raises(ValueError):
        _research_change_block("energy",100,"weapons")
