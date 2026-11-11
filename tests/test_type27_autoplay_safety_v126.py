from stars_ai.adapters.stars_native import NativeBlock
from stars_ai.design_legality import ComponentRef
import pytest
from stars_ai.native.design_change import (
    EncodedShipDesign,
    create_ship_design_blocks,
    create_starbase_design_blocks,
    encoded_ship_design_from_payload,
    parse_design_change_payload,
)
from stars_ai.native.starsapi_design_codec import decode_design_block, encode_stars_string
from stars_ai.native.x_writer import (
    TYPE27_AUTOPLAY_ENABLED,
    _frame_starbase_design_transaction,
)


def _space_station() -> EncodedShipDesign:
    return EncodedShipDesign(
        slot=2, hull_id=34, pic=136, armor=500, turn_designed=0,
        slots=(
            ComponentRef(512, 0, 1), ComponentRef(16, 0, 16),
            ComponentRef(4, 0, 16), ComponentRef(16, 0, 16),
            ComponentRef(4, 0, 16), ComponentRef(4, 0, 16),
            ComponentRef(2048, 5, 3), ComponentRef(16, 0, 16),
            ComponentRef(2048, 5, 3), ComponentRef(16, 0, 16),
            ComponentRef(512, 0, 1), ComponentRef(4, 0, 16),
        ),
        name="Modernized Space Station Defense", staging_name="Space Station",
        is_starbase=True,
    )


def _yakimora_space_station() -> EncodedShipDesign:
    """Existing P4 baseline captured before its Blackjack starbase upgrade."""
    desired = _space_station()
    return EncodedShipDesign(
        slot=3, hull_id=34, pic=136, armor=500, turn_designed=32,
        slots=tuple(
            ComponentRef(component.category, 3, component.count)
            if component.category == 16 else component
            for component in desired.slots
        ),
        name="Existing Space Station", staging_name="Existing Space Station",
        is_starbase=True,
    )

def test_autoplay_enables_host_replayed_type27_design_lifecycle():
    assert TYPE27_AUTOPLAY_ENABLED is True


@pytest.mark.parametrize("player_id,controls", [
    (1, (bytes.fromhex("01 B2"), bytes.fromhex("01 72"))),
    (2, (bytes.fromhex("11 B2"), bytes.fromhex("11 72"))),
    (3, (bytes.fromhex("21 B2"), bytes.fromhex("21 72"))),
    (4, (bytes.fromhex("31 B2"), bytes.fromhex("31 72"))),
    (5, (bytes.fromhex("41 B2"), bytes.fromhex("41 72"))),
])
def test_starbase_creation_uses_validated_two_record_controls_for_every_ai_player(
    player_id, controls,
):
    design = _space_station()
    seed = _yakimora_space_station()
    initial, populated = create_starbase_design_blocks(
        design, player_id=player_id, baseline_design=seed,
    )

    assert [block.data[:2] for block in (initial, populated)] == list(controls)
    parsed_initial = parse_design_change_payload(initial.data)
    parsed_populated = parse_design_change_payload(populated.data)
    assert parsed_initial.is_starbase
    assert tuple(parsed_initial.slots) == seed.slots
    assert parsed_initial.embedded_slot_byte == parsed_populated.embedded_slot_byte == 0x49
    assert parsed_populated.is_starbase
    assert parsed_populated.armor == 500
    assert tuple(parsed_populated.slots) == design.slots
    initial_name = decode_design_block(
        initial.data[2:], allow_type27_bit0_clear=True,
    ).name
    populated_name = decode_design_block(
        populated.data[2:], allow_type27_bit0_clear=True,
    ).name
    assert initial_name == populated_name == "Base 03"
    assert len(encode_stars_string(populated_name)) == 6


def test_first_starbase_hull_uses_an_empty_bn_body_then_the_fitted_commit():
    fitted = EncodedShipDesign(
        slot=1, hull_id=32, pic=138, armor=128, turn_designed=0,
        slots=(
            ComponentRef(0, 0, 0), ComponentRef(16, 0, 1),
            ComponentRef(0, 0, 0), ComponentRef(0, 0, 0), ComponentRef(0, 0, 0),
        ),
        name="Base 02", staging_name="Base 02", is_starbase=True,
    )
    empty = EncodedShipDesign(
        slot=1, hull_id=32, pic=138, armor=128, turn_designed=0,
        slots=tuple(ComponentRef(0, 0, 0) for _ in fitted.slots),
        name="Base 02", staging_name="Base 02", is_starbase=True,
    )

    initial, populated = create_starbase_design_blocks(
        fitted, player_id=1, baseline_design=empty,
    )

    assert initial.data[:2] == bytes.fromhex("01 B1")
    assert populated.data[:2] == bytes.fromhex("01 71")
    assert all(int(slot.count) == 0 for slot in parse_design_change_payload(initial.data).slots)
    assert tuple(parse_design_change_payload(populated.data).slots)[1] == ComponentRef(16, 0, 1)
    assert decode_design_block(populated.data[2:], allow_type27_bit0_clear=True).name == "Base 02"


def test_starbase_framing_keeps_ordinary_orders_and_copies_only_known_submit():
    ordinary = [NativeBlock(4, 4, bytes.fromhex("00 00 00 00"))]
    changes = create_starbase_design_blocks(
        _space_station(), player_id=1, baseline_design=_yakimora_space_station(),
    )
    submit = NativeBlock(46, 4, bytes.fromhex("01 01 05 19"))

    with_submit = _frame_starbase_design_transaction(ordinary, changes, [submit])
    without_submit = _frame_starbase_design_transaction(ordinary, changes, [])

    assert [block.type_id for block in with_submit] == [27, 27, 4, 46]
    assert [block.type_id for block in without_submit] == [27, 27, 4, 46]


@pytest.mark.parametrize("is_starbase,create,slot", [
    (False, create_ship_design_blocks, 6),
    (True, create_starbase_design_blocks, 3),
])
def test_type27_design_creation_never_serializes_blank_ship_or_starbase_names(
    is_starbase, create, slot,
):
    design = EncodedShipDesign(
        slot=slot, hull_id=34 if is_starbase else 4, pic=136 if is_starbase else 4,
        armor=500 if is_starbase else 50, turn_designed=0,
        slots=(ComponentRef(0, 0, 0),) if not is_starbase else _space_station().slots,
        name=" \x1f\t ", staging_name="\n", is_starbase=is_starbase,
    )

    if is_starbase:
        initial, populated = create(
            design, player_id=1, baseline_design=_yakimora_space_station(),
        )
    else:
        initial, populated = create(design, player_id=1)
    initial_name = decode_design_block(
        initial.data[2:], allow_type27_bit0_clear=True,
    ).name
    populated_name = decode_design_block(
        populated.data[2:], allow_type27_bit0_clear=True,
    ).name
    expected = f"{'Starbase' if is_starbase else 'Ship'} Design {slot + 1}"
    wire_expected = f"Base {slot + 1:02d}" if is_starbase else expected

    assert design.name == design.staging_name == expected
    assert initial_name == populated_name == wire_expected


def test_design_payload_without_a_name_receives_a_visible_ship_name():
    design = encoded_ship_design_from_payload({
        "target_slot": 4, "hull_id": 4, "pic": 4, "armor": 50,
        "slots": [{"category": 0, "item_id": 0, "count": 0}],
    })

    assert design.name == design.staging_name == "Ship Design 5"
