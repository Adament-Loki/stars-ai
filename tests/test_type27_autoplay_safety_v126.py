from stars_ai.adapters.stars_native import NativeBlock
from stars_ai.design_legality import ComponentRef
from stars_ai.native.design_change import (
    EncodedShipDesign,
    create_starbase_design_blocks,
    parse_design_change_payload,
)
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


def test_autoplay_enables_host_replayed_type27_design_lifecycle():
    assert TYPE27_AUTOPLAY_ENABLED is True


def test_starbase_creation_uses_captured_four_record_controls_and_bodies():
    design = _space_station()
    initial, delete, populated, committed = create_starbase_design_blocks(design, player_id=1)

    assert [block.data[:2] for block in (initial, delete, populated, committed)] == [
        bytes.fromhex("01 B2"), bytes.fromhex("00 72"),
        bytes.fromhex("01 B2"), bytes.fromhex("01 72"),
    ]
    parsed_initial = parse_design_change_payload(initial.data)
    parsed_delete = parse_design_change_payload(delete.data)
    parsed_populated = parse_design_change_payload(populated.data)
    parsed_committed = parse_design_change_payload(committed.data)
    assert parsed_initial.is_starbase and all(slot.count == 0 for slot in parsed_initial.slots)
    assert parsed_delete.delete and parsed_delete.is_starbase and parsed_delete.design_slot == 2
    assert parsed_populated.embedded_slot_byte == parsed_committed.embedded_slot_byte == 0x49
    assert parsed_committed.is_starbase
    assert parsed_committed.armor == 500
    assert tuple(parsed_committed.slots) == design.slots


def test_starbase_framing_keeps_ordinary_orders_and_copies_only_known_submit():
    ordinary = [NativeBlock(4, 4, bytes.fromhex("00 00 00 00"))]
    changes = create_starbase_design_blocks(_space_station(), player_id=1)
    submit = NativeBlock(46, 4, bytes.fromhex("01 01 05 19"))

    with_submit = _frame_starbase_design_transaction(ordinary, changes, [submit])
    without_submit = _frame_starbase_design_transaction(ordinary, changes, [])

    assert [block.type_id for block in with_submit] == [4, 46, 27, 27, 27, 27, 46]
    assert [block.type_id for block in without_submit] == [4, 27, 27, 27, 27]
