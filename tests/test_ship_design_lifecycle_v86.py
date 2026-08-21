import pytest

from stars_ai.design_development import add_design_development_orders, plan_design_development
from stars_ai.design_legality import ComponentRef
from stars_ai.models import Fleet, GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.native.design_change import (
    EncodedShipDesign,
    UnsafeShipDesignMutationError,
    assert_deletable_ship_design_slot,
    assert_free_ship_design_slot,
    create_ship_design_blocks,
    delete_existing_ship_design_block,
    parse_design_change_payload,
)
from stars_ai.ship_design_synth import synthesize_onion_privateer, synthesize_scout_upgrade


def _base_state(*, construction=4):
    home = Planet(
        0, "Home", Position(0, 0), owner=1, habitability=100, population=120_000,
        ironium=1000, boranium=1000, germanium=1000,
        native={"is_homeworld": True, "starbase_capabilities": {"can_build_ships": True, "can_refuel": True}},
    )
    child = Planet(1, "Child", Position(100, 0), owner=1, habitability=80, population=20_000, native={})
    return GameState(
        "v86", 2425, 1, RaceProfile(native={"lrts": ["IFE"]}),
        Tech(propulsion=2, construction=construction), [home, child], [],
        native={"designs": [], "design_profiles": [], "production_by_planet": {}},
    )


def test_client_generated_privateer_free_slot_fixture_is_reproduced_exactly():
    # Decoded directly from user-provided GAME.x2, turn 25, ship slot 4.
    design = EncodedShipDesign(
        slot=4, hull_id=11, pic=44, armor=150, turn_designed=25,
        slots=(
            ComponentRef(1, 2, 1),      # Fuel Mizer
            ComponentRef(4, 0, 2),      # two shields in the controlled sample
            ComponentRef(4096, 5, 1),   # Fuel Tank
            ComponentRef(4096, 5, 1),
            ComponentRef(4096, 5, 1),
        ),
        name="Long Range Privateer",
        staging_name="Privateer",
    )
    staging, final = create_ship_design_blocks(design, final_control=0x64)
    assert staging.data == bytes.fromhex(
        "11 A4 07 10 0B 2C 96 00 05 19 00 00 00 00 00 00 00 00 00 "
        "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
        "06 BF 84 DF 1A 22 8F"
    )
    assert final.data == bytes.fromhex(
        "11 64 07 10 0B 2C 96 00 05 19 00 00 00 00 00 00 00 00 00 "
        "01 00 02 01 04 00 00 02 00 10 05 01 00 10 05 01 00 10 05 01 "
        "0D BB 76 D8 0C 11 6D 82 0B F8 4D F1 A2 28"
    )
    assert parse_design_change_payload(staging.data).control == bytes.fromhex("11 A4")
    assert parse_design_change_payload(final.data).control == bytes.fromhex("11 64")


def test_atomic_replace_is_not_an_encoder_operation_anymore():
    design = EncodedShipDesign(
        4, 11, 44, 150, 25,
        (ComponentRef(1, 2, 1),) + (ComponentRef(0, 0, 0),) * 4,
        "Onion Privateer", "Privateer", True,
    )
    with pytest.raises(ValueError, match="Atomic replace is forbidden"):
        create_ship_design_blocks(design)


def test_delete_block_is_owner_aware_two_byte_form():
    assert delete_existing_ship_design_block(3,player_id=1).data == bytes.fromhex("00 03")
    assert delete_existing_ship_design_block(3).data == bytes.fromhex("10 03")
    assert delete_existing_ship_design_block(3,player_id=3).data == bytes.fromhex("20 03")


def test_writer_safety_refuses_delete_while_any_live_ship_uses_design():
    state = _base_state()
    state.native["designs"] = [{"design_number": 3, "name": "Old Scout", "is_starbase": False, "total_remaining": 0}]
    state.fleets = [Fleet(1, "Still Alive", 1, Position(0, 0), native={"ship_count": [0, 0, 0, 1]})]
    with pytest.raises(UnsafeShipDesignMutationError) as exc:
        assert_deletable_ship_design_slot(state, 3)
    assert exc.value.diagnostic["live_ship_count"] == 1


def test_writer_safety_refuses_delete_while_design_is_queued():
    state = _base_state()
    state.native["designs"] = [{"design_number": 3, "name": "Old Scout", "is_starbase": False, "total_remaining": 0}]
    state.native["production_by_planet"] = {"0": [{"item_type": 4, "item_id": 3, "count": 2}]}
    with pytest.raises(UnsafeShipDesignMutationError) as exc:
        assert_deletable_ship_design_slot(state, 3)
    assert exc.value.diagnostic["queued_ship_count"] == 2


def test_writer_safety_allows_only_truly_dead_design_delete():
    state = _base_state()
    state.native["designs"] = [{"design_number": 3, "name": "Dead", "is_starbase": False, "total_remaining": 0}]
    safe = assert_deletable_ship_design_slot(state, 3)
    assert safe.design_exists and safe.live_ship_count == safe.queued_ship_count == 0


def test_writer_safety_refuses_create_into_occupied_slot_even_if_planner_says_free():
    state = _base_state()
    state.native["designs"] = [{"design_number": 4, "name": "Occupied", "is_starbase": False, "total_remaining": 0}]
    with pytest.raises(UnsafeShipDesignMutationError):
        assert_free_ship_design_slot(state, 4)


def test_free_slot_privateer_is_create_not_replace():
    state = _base_state()
    plan = synthesize_onion_privateer(state)
    assert plan is not None and plan.encoded.replace_existing is False
    orders = OrderSet(state.game_name, state.year, state.player_id)
    add_design_development_orders(state, orders)
    executable = [o for o in orders.orders if o.kind in {"create_ship_design", "delete_ship_design", "replace_ship_design"}]
    assert executable
    assert executable[0].kind == "create_ship_design"
    assert executable[0].payload["replace_existing"] is False


def test_full_slots_recycle_is_delete_only_then_wait_for_readback():
    state = _base_state()
    state.native["designs"] = [
        {"design_number": i, "name": f"Dead{i}", "is_starbase": False, "total_remaining": 0, "turn_designed": i, "hull_id": 0, "slots": []}
        for i in range(16)
    ]
    orders = OrderSet(state.game_name, state.year, state.player_id)
    add_design_development_orders(state, orders)
    executable = [o for o in orders.orders if o.kind in {"create_ship_design", "delete_ship_design", "replace_ship_design"}]
    assert executable
    assert executable[0].kind == "delete_ship_design"
    assert "next-M" in executable[0].reason
    assert all(o.kind != "replace_ship_design" for o in executable)


def test_fuel_mizer_scout_is_not_proposed_when_best_existing_w7_scout_is_better():
    state = _base_state(construction=4)
    state.planets.append(Planet(2, "Unknown", Position(150, 30), owner=None, observed=False))
    # Daddy Long Legs 7 + scanner + Fuel Tank.  This is heavier than a Fuel
    # Mizer clone but has materially better W7 fuel efficiency, matching the
    # playtest scout that exposed the v8.5 comparison bug.
    state.native["designs"] = [{
        "design_number": 0, "name": "Peeping Tom", "hull_id": 4, "is_starbase": False,
        "total_remaining": 0, "turn_designed": 0,
        "slots": [
            {"category": 1, "item_id": 4, "count": 1},
            {"category": 2, "item_id": 0, "count": 1},
            {"category": 4096, "item_id": 5, "count": 1},
        ],
    }]
    state.native["design_profiles"] = [{
        "design_number": 0, "name": "Peeping Tom", "role": "scout", "hull_id": 4,
        "dry_mass": 26, "cargo_capacity": 0, "fuel_capacity": 300,
        "engine_id": 4, "ram_scoop": False,
    }]
    assert synthesize_scout_upgrade(state) is None
    assert not any(p.role == "scout" for p in plan_design_development(state))
