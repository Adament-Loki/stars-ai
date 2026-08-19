
from stars_ai.native.x_writer import RESEARCH_FIELD_CODES, _research_change_block

def test_full_research_mapping_enabled():
    assert RESEARCH_FIELD_CODES == {
        "energy":0x60,
        "weapons":0x61,
        "propulsion":0x62,
        "construction":0x63,
        "electronics":0x64,
        "biotechnology":0x65,
    }

def test_all_research_fields_encode_100_percent_switch():
    for field, code in RESEARCH_FIELD_CODES.items():
        b=_research_change_block(field,100)
        assert b.type_id==34
        assert b.data==bytes([0x0F,code])

def test_propulsion_empirical_payload():
    assert _research_change_block("propulsion",100).data==bytes.fromhex("0F 62")
