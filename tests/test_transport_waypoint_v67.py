
from stars_ai.models import GameState,RaceProfile,Tech,Planet,Fleet,Position
from stars_ai.native.x_writer import (
    TRANSPORT_POPULATION_UNLOAD_ALL,
    TRANSPORT_UNLOAD_ALL_CARGO,
    _annotate_untrusted_emissions,
    _population_emitted_payload,
    _transport_population_blocks,
    _transport_mineral_blocks,
)
from stars_ai.native.population_transport import (
    MAX_MEDIUM_MANIFEST_FIELD_KT,
    medium_load_block,
    small_population_load_block,
)

def state():
    return GameState(
        "g",2400,1,RaceProfile(),Tech(),
        [
            Planet(0,"Source",Position(1000,1000),owner=1,observed=True),
            Planet(38,"Target",Position(1228,1568),owner=1,observed=True),
        ],
        [
            Fleet(
                3,"Swashbucker 4",1,Position(1000,1000),role="freighter",
                cargo_capacity=250,
                native={"waypoint_count":1,"waypoints":[
                    {"x":1000,"y":1000,"position_object":0,"warp":0,"task":0,"position_object_type":0x11}
                ]},
            )
        ],
    )

def test_exact_controlled_transport_directives():
    # The live-client control from turn 035 sends an empty freighter to Chunk
    # with every cargo type set to Unload All.  There is no inferred fuel
    # directive after the four 00 20 cargo fields.
    assert TRANSPORT_UNLOAD_ALL_CARGO == bytes.fromhex("00 20 00 20 00 20 00 20")

def test_destination_waypoint_carries_complete_transport_policy():
    blocks=_transport_mineral_blocks(
        state(),
        {"fleet_id":3,"destination_planet_id":38,"warp":5},
    )
    assert [b.type_id for b in blocks]==[1,4,5]
    task=blocks[-1]
    assert task.data[10] & 0x0F == 1
    assert task.data[-8:]==TRANSPORT_UNLOAD_ALL_CARGO

def test_new_controlled_file_confirms_20_20_20_manual_load_values():
    # GAME(2).x1 Type 1 payload for P1 Fleet #4:
    observed=bytes.fromhex("03 00 25 00 12 07 14 14 14")
    assert observed[-3:]==bytes([20,20,20])


def test_200kt_population_transport_uses_the_general_four_cargo_unload_endpoint():
    blocks=_transport_population_blocks(
        state(), {"fleet_id":3,"source_planet_id":0,"destination_planet_id":38,"warp":5,"population_kt":200},
    )
    assert [b.type_id for b in blocks] == [2,4,5]
    assert blocks[0].data == bytes.fromhex("03 00 00 00 12 08 c8 00")
    assert blocks[1].data[10:] == bytes.fromhex("50 51")
    assert blocks[2].data[10:12] == bytes.fromhex("51 51")
    assert blocks[2].data[-8:] == TRANSPORT_UNLOAD_ALL_CARGO


def test_80kt_population_transport_uses_type1_load_with_general_unload_endpoint():
    blocks=_transport_population_blocks(
        state(), {"fleet_id":3,"source_planet_id":0,"destination_planet_id":38,"warp":5,"population_kt":80},
    )
    assert [b.type_id for b in blocks] == [1,4,5]
    assert blocks[0].data == bytes.fromhex("03 00 00 00 12 08 50")
    assert blocks[1].data[10:] == bytes.fromhex("50 51")
    assert blocks[-1].data[-8:] == TRANSPORT_UNLOAD_ALL_CARGO


def test_80kt_client_capture_is_preserved_exactly():
    assert small_population_load_block(state(),3,80,source_planet_id=87).data == bytes.fromhex(
        "03 00 57 00 12 08 50"
    )


def test_population_load_type_changes_at_the_host_verified_127kt_boundary():
    lower=_transport_population_blocks(
        state(), {"fleet_id":3,"source_planet_id":87,"destination_planet_id":38,"warp":5,"population_kt":127},
    )
    upper=_transport_population_blocks(
        state(), {"fleet_id":3,"source_planet_id":87,"destination_planet_id":38,"warp":5,"population_kt":128},
    )
    assert lower[0].type_id == 1
    assert lower[0].data == bytes.fromhex("03 00 57 00 12 08 7f")
    assert upper[0].type_id == 2
    assert upper[0].data == bytes.fromhex("03 00 57 00 12 08 80 00")


def test_80kt_population_only_trace_is_validated_after_end_to_end_host_replay():
    blocks=_transport_population_blocks(
        state(), {"fleet_id":3,"source_planet_id":0,"destination_planet_id":38,"warp":5,"population_kt":80},
    )
    event={
        "kind":"transport_population",
        "payload":_population_emitted_payload(
            {"fleet_id":3,"source_planet_id":0,"population_kt":80,"mineral_load":{}},blocks,"ADD"
        ),
    }
    _annotate_untrusted_emissions([event])
    assert event["payload"]["native_experiment"]["trust_level"]=="VALIDATED"
    assert event["native_capability"]["status"]=="VALIDATED"


def test_population_transport_fills_residual_hold_with_capacity_bounded_minerals():
    blocks=_transport_population_blocks(
        state(), {
            "fleet_id":3,
            "source_planet_id":0,
            "destination_planet_id":38,
            "warp":5,
            "population_kt":200,
            "mineral_load":{"ironium":20,"boranium":20,"germanium":10},
        },
    )
    assert [b.type_id for b in blocks] == [2,4,5]
    # One client-observed Type-2 mask carries every selected I/B/G/population
    # amount as u16 quantities; no competing Type-1 mineral order is needed.
    assert blocks[0].data == bytes.fromhex("03 00 00 00 12 0f 14 00 14 00 0a 00 c8 00")
    # Mixed cargo uses the client-observed transport endpoint so every cargo
    # type unloads, including an absent cargo type.
    assert blocks[1].data[10:] == bytes.fromhex("50 51")
    assert blocks[-1].data[-8:] == TRANSPORT_UNLOAD_ALL_CARGO


def test_experimental_population_trace_contains_exact_blocks_and_capability_status():
    blocks=_transport_population_blocks(
        state(), {
            "fleet_id":3,"source_planet_id":0,"destination_planet_id":38,"warp":5,"population_kt":80,
            "mineral_load":{"ironium":100,"boranium":50,"germanium":20},
            "native_experiment":{"id":"population-type2-80kt-with-minerals"},
        },
    )
    event={
        "kind":"transport_population",
        "payload":_population_emitted_payload({
            "fleet_id":3,"source_planet_id":0,"population_kt":80,
            "mineral_load":{"ironium":100,"boranium":50,"germanium":20},
            "native_experiment":{"id":"population-type2-80kt-with-minerals"},
        },blocks,"ADD"),
    }
    _annotate_untrusted_emissions([event])
    experiment=event["payload"]["native_experiment"]
    assert experiment["trust_level"]=="EXPERIMENTAL"
    assert experiment["waypoint_route_type"]=="0x51"
    assert experiment["block_sequence"][0]["data_hex"]=="03 00 00 00 12 0f 64 00 32 00 14 00 50 00"
    assert event["native_capability"]["status"]=="EXPERIMENTAL"
    assert event["native_capability"]["trace_required"] is True


def test_sandbox_x1_client_capture_proves_the_mixed_type2_selection_mask():
    # Native client capture: Type37 fleet 7 absorbs fleet 8, then the retained fleet
    # loads B=70, G=100, Population=330 in one 500 kT Type-2 manifest.
    block=medium_load_block(
        state(), 3, {"boranium":70,"germanium":100,"population":330},
        source_planet_id=151,
        capacity_override=500,
    )
    assert block.data == bytes.fromhex("03 00 97 00 12 0e 46 00 64 00 4a 01")


def test_type2_has_a_u16_per_cargo_field_without_an_inferred_1000kt_cutoff():
    block=medium_load_block(
        state(),
        3,
        {"ironium":MAX_MEDIUM_MANIFEST_FIELD_KT},
        source_planet_id=151,
        capacity_override=MAX_MEDIUM_MANIFEST_FIELD_KT,
    )
    assert block.data == bytes.fromhex("03 00 97 00 12 01 ff ff")

    try:
        medium_load_block(
            state(),
            3,
            {"ironium":MAX_MEDIUM_MANIFEST_FIELD_KT + 1},
            source_planet_id=151,
            capacity_override=MAX_MEDIUM_MANIFEST_FIELD_KT + 1,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Type-2 amount fields must remain u16 until Type-25 is evidenced")
