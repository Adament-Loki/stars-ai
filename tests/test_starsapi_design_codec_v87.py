import pytest

from stars_ai.design_legality import ComponentRef
from stars_ai.native.design_change import EncodedShipDesign, create_ship_design_blocks
from stars_ai.native.starsapi_design_codec import (
    StarsApiDesign,
    decode_design_block,
    encode_design_block,
    encode_type27_embedded_design,
    starsapi_body_roundtrip,
)


def test_starsapi_normal_designblock_matches_client_fresh_colony_body_after_bit_restore():
    # User-provided GAME.x2 fresh Colony Ship FINAL Type27 body is 07 10 ...
    # inside DesignChange. StarsAPI DesignChangeBlock.decode() restores byte1
    # bit0 before delegating to DesignBlock.decode(), so the ordinary
    # DesignBlock encoding must be the same body with 10 -> 11.
    client_embedded = bytes.fromhex(
        "07 10 0f 3c 14 00 02 02 00 00 00 00 00 00 00 00 00 "
        "01 00 02 01 00 10 00 01 08 b2 75 76 e2 0c 23 4d cf"
    )
    d = StarsApiDesign(
        design_number=4,
        hull_id=15,
        pic=60,
        armor=20,
        slot_count=2,
        turn_designed=2,
        total_built=0,
        total_remaining=0,
        slots=(ComponentRef(1, 2, 1), ComponentRef(4096, 0, 1)),
        name="Colony Ship",
    )
    normal = encode_design_block(d)
    expected = bytearray(client_embedded)
    expected[1] |= 1
    assert normal == bytes(expected)
    assert encode_type27_embedded_design(d) == client_embedded


def test_starsapi_type27_decoder_records_client_bit0_clear_and_roundtrips_exactly():
    body = bytes.fromhex(
        "07 10 0f 3c 14 00 02 02 00 00 00 00 00 00 00 00 00 "
        "01 00 02 01 00 10 00 01 08 b2 75 76 e2 0c 23 4d cf"
    )
    parsed = decode_design_block(body, allow_type27_bit0_clear=True)
    assert parsed.design_number == 4
    assert parsed.type27_bit0_was_clear is True
    assert parsed.raw_second_byte == 0x10
    assert parsed.normalized_second_byte == 0x11
    assert parsed.name == "Colony Ship"
    assert starsapi_body_roundtrip(body, type27_embedded=True) == body


def test_starsapi_port_reproduces_client_privateer_fixture_body_via_design_change_api():
    d = EncodedShipDesign(
        slot=4,
        hull_id=11,
        pic=44,
        armor=150,
        turn_designed=25,
        slots=(
            ComponentRef(1, 2, 1),
            ComponentRef(4, 0, 2),
            ComponentRef(4096, 5, 1),
            ComponentRef(4096, 5, 1),
            ComponentRef(4096, 5, 1),
        ),
        name="Long Range Privateer",
        staging_name="Privateer",
    )
    staging, final = create_ship_design_blocks(d)
    assert staging.data[2:] == bytes.fromhex(
        "07 10 0B 2C 96 00 05 19 00 00 00 00 00 00 00 00 00 "
        "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
        "06 BF 84 DF 1A 22 8F"
    )
    assert final.data[2:] == bytes.fromhex(
        "07 10 0B 2C 96 00 05 19 00 00 00 00 00 00 00 00 00 "
        "01 00 02 01 04 00 00 02 00 10 05 01 00 10 05 01 00 10 05 01 "
        "0D BB 76 D8 0C 11 6D 82 0B F8 4D F1 A2 28"
    )


def test_failed_p1_turn3_onion_privateer_body_is_valid_starsapi_designblock():
    # This is the populated Type27 body from the failed P1 Turn-3 X.  It proves
    # an important diagnostic point: the body itself conforms to StarsAPI.  If
    # v8.7 emits this same body and the host still rejects the X, the remaining
    # defect is outside DesignBlock layout (Type27 wrapper/order semantics).
    body = bytes.fromhex(
        "07 10 0b 2c 96 00 05 02 00 00 00 00 00 00 00 00 00 "
        "01 00 02 01 00 00 00 00 00 10 05 01 00 10 05 01 00 10 05 01 "
        "09 be 64 76 0b f8 4d f1 a2 28"
    )
    parsed = decode_design_block(body, allow_type27_bit0_clear=True)
    assert parsed.design_number == 4
    assert parsed.hull_id == 11
    assert parsed.slot_count == 5
    assert parsed.name == "Onion Privateer"
    assert starsapi_body_roundtrip(body, type27_embedded=True) == body


def test_starsapi_decoder_rejects_trailing_or_malformed_body_instead_of_guessing():
    good = bytes.fromhex(
        "07 10 0f 3c 14 00 02 02 00 00 00 00 00 00 00 00 00 "
        "01 00 02 01 00 10 00 01 08 b2 75 76 e2 0c 23 4d cf"
    )
    with pytest.raises(ValueError, match="Unexpected StarsAPI design size"):
        decode_design_block(good + b"\x00", allow_type27_bit0_clear=True)
    malformed = bytearray(good)
    malformed[0] = 0x17
    with pytest.raises(ValueError, match="first byte"):
        decode_design_block(bytes(malformed), allow_type27_bit0_clear=True)
