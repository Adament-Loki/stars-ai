from stars_ai.design_development import plan_design_development
from stars_ai.design_legality import validate_design
from stars_ai.models import GameState, Planet, Position, RaceProfile, Tech
from stars_ai.native.design_change import (
    UnsafeShipDesignMutationError,
    assert_free_starbase_design_slot,
    create_starbase_design_blocks,
    parse_design_change_payload,
    starbase_design_slot_safety,
)
from stars_ai.ship_design_synth import synthesize_generic_design_proposal
from stars_ai.starbase_capabilities import starbase_capabilities
from stars_ai.starbase_planner import plan_support_base_builds


def _state(*, isb: bool = False) -> GameState:
    race = RaceProfile(native={"lrts": ["ISB"] if isb else []})
    home = Planet(
        0, "Home", Position(0, 0), owner=1, population=180_000,
        ironium=2_000, boranium=2_000, germanium=2_000,
        native={
            "is_homeworld": True,
            "has_starbase": True,
            "starbase_design": 0,
            "starbase_capabilities": starbase_capabilities(32),
        },
    )
    frontier = Planet(
        1, "Frontier", Position(180, 0), owner=1, population=70_000,
        ironium=1_500, boranium=1_500, germanium=1_500,
    )
    return GameState(
        "starbase-create", 2420, 1, race, Tech(construction=8, weapons=6, energy=6),
        [home, frontier], [], native={
            "designs": [{
                "design_number": 0, "name": "Orbital Fort", "hull_id": 32,
                "is_starbase": True, "total_remaining": 0, "slots": [],
            }],
            "design_profiles": [],
            "starbase_profiles": [{
                "design_number": 0, "name": "Orbital Fort", "hull_id": 32,
                "hull_name": "Orbital Fort", "capabilities": starbase_capabilities(32),
                "slots": [],
            }],
            "production_by_planet": {},
        },
    )


def test_normal_race_proposes_and_compiles_a_custom_space_station():
    state = _state()
    proposal = next(proposal for proposal in plan_design_development(state) if proposal.role == "starbase")

    assert proposal.desired_hull_id == 34
    plan = synthesize_generic_design_proposal(state, proposal.to_payload())

    assert plan is not None
    assert plan.encoded.is_starbase is True
    assert plan.encoded.hull_id == 34
    assert plan.encoded.slot == 1
    assert len(plan.encoded.slots) == 12
    assert any(slot.count for slot in plan.encoded.slots)
    assert validate_design(plan.encoded.hull_id, plan.encoded.slots).ok

    staging, final = create_starbase_design_blocks(plan.encoded, player_id=1)
    parsed_staging = parse_design_change_payload(staging.data)
    parsed_final = parse_design_change_payload(final.data)
    assert staging.data[:2] == bytes.fromhex("01 A1")
    assert final.data[:2] == bytes.fromhex("01 A1")
    assert parsed_staging.is_starbase and parsed_final.is_starbase
    assert parsed_final.design_slot == 1
    assert parsed_final.hull_id == 34
    assert all(slot.count == 0 for slot in parsed_staging.slots)
    assert tuple(parsed_final.slots) == plan.encoded.slots


def test_isb_prefers_a_custom_space_dock_when_no_support_design_exists():
    state = _state(isb=True)
    proposal = next(proposal for proposal in plan_design_development(state) if proposal.role == "starbase")

    assert proposal.desired_hull_id == 33
    plan = synthesize_generic_design_proposal(state, proposal.to_payload())
    assert plan is not None
    assert plan.encoded.is_starbase is True
    assert plan.encoded.hull_id == 33


def test_newly_read_back_custom_design_is_usable_by_the_base_queue_planner():
    """Creation and construction are intentionally separated by M-file read-back."""
    state = _state()
    proposal = next(proposal for proposal in plan_design_development(state) if proposal.role == "starbase")
    design = synthesize_generic_design_proposal(state, proposal.to_payload()).encoded
    slots = [
        {"category": slot.category, "item_id": slot.item_id, "count": slot.count}
        for slot in design.slots
    ]
    state.native["designs"].append({
        "design_number": design.slot, "name": design.name, "hull_id": design.hull_id,
        "is_starbase": True, "total_remaining": 0, "slots": slots,
    })
    state.native["starbase_profiles"].append({
        "design_number": design.slot, "name": design.name, "hull_id": design.hull_id,
        "hull_name": "Space Station", "capabilities": starbase_capabilities(design.hull_id),
        "slots": slots,
    })

    requests = plan_support_base_builds(state)

    assert requests
    assert all(request.design_slot == design.slot for request in requests)


def test_starbase_slot_safety_refuses_installed_and_queued_base_designs():
    state = _state()
    occupied = starbase_design_slot_safety(state, 0)
    assert occupied.design_exists
    assert occupied.installed_starbase_count == 1
    try:
        assert_free_starbase_design_slot(state, 0)
    except UnsafeShipDesignMutationError as exc:
        assert exc.diagnostic["installed_starbase_count"] == 1
    else:
        raise AssertionError("installed starbase design slot was incorrectly considered free")

    state.native["production_by_planet"] = {"1": [{"item_type": 4, "item_id": 18, "count": 1}]}
    queued = starbase_design_slot_safety(state, 2)
    assert queued.queued_starbase_count == 1
