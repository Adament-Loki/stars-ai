from stars_ai.design_legality import ComponentRef
from stars_ai.native.design_change import EncodedShipDesign, create_ship_design_blocks


def test_turn3_client_fresh_colony_ship_fixture_matches_exact_create_pair():
    # User-provided GAME.x2, year 2402, player 2.  After an unrelated temporary
    # Mayflower copy/delete in the same X, the fresh Colony Ship creation itself
    # is exactly A4 staging -> 64 final with bit0-clear embedded design slot.
    d = EncodedShipDesign(
        slot=4,
        hull_id=15,
        pic=60,
        armor=20,
        turn_designed=2,
        slots=(
            ComponentRef(1, 2, 1),       # Fuel Mizer
            ComponentRef(4096, 0, 1),    # Colonization Module
        ),
        name="Colony Ship",
        staging_name="Colony Ship",
    )
    staging, final = create_ship_design_blocks(d, final_control=0x64)
    assert staging.data == bytes.fromhex(
        "11 a4 07 10 0f 3c 14 00 02 02 00 00 00 00 00 00 00 00 00 "
        "00 00 00 00 00 00 00 00 08 b2 75 76 e2 0c 23 4d cf"
    )
    assert final.data == bytes.fromhex(
        "11 64 07 10 0f 3c 14 00 02 02 00 00 00 00 00 00 00 00 00 "
        "01 00 02 01 00 10 00 01 08 b2 75 76 e2 0c 23 4d cf"
    )

from stars_ai.native.design_change import parse_design_change_payload


def test_client_copy_record_is_distinct_from_fresh_create_body():
    copied = parse_design_change_payload(bytes.fromhex(
        "11 a4 07 11 0f 3e 14 00 02 02 00 00 00 00 00 00 00 00 00 "
        "01 00 04 01 00 10 00 01 0b bc 1e 2d 75 7e 02 80 f8 2c cf 92"
    ))
    fresh = parse_design_change_payload(bytes.fromhex(
        "11 a4 07 10 0f 3c 14 00 02 02 00 00 00 00 00 00 00 00 00 "
        "00 00 00 00 00 00 00 00 08 b2 75 76 e2 0c 23 4d cf"
    ))
    assert copied.embedded_slot_byte == 0x11
    assert fresh.embedded_slot_byte == 0x10
    assert copied.embedded_slot_byte & 1
    assert not (fresh.embedded_slot_byte & 1)
