
from stars_ai.native.x_writer import RESEARCH_FIELD_CODES, _research_change_block

def test_full_research_mapping_enabled():
    assert RESEARCH_FIELD_CODES == {
        "energy":0,
        "weapons":1,
        "propulsion":2,
        "construction":3,
        "electronics":4,
        "biotechnology":5,
    }

def test_all_research_fields_encode_15_percent_current_and_next():
    for field, code in RESEARCH_FIELD_CODES.items():
        b=_research_change_block(field,15,field)
        assert b.type_id==34
        assert b.data==bytes([0x0F,(code << 4) | code])

def test_propulsion_empirical_payload():
    assert _research_change_block("propulsion",15,"weapons").data==bytes.fromhex("0F 12")
