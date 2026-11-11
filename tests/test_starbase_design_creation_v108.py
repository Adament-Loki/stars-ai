from stars_ai.design_development import plan_design_development
from stars_ai.design_legality import validate_design
from stars_ai.models import Fleet, GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.native.design_change import (
    UnsafeShipDesignMutationError,
    assert_free_starbase_design_slot,
    create_starbase_design_blocks,
    parse_design_change_payload,
    starbase_design_slot_safety,
)
from stars_ai.ship_design_synth import synthesize_generic_design_proposal
from stars_ai.starbase_capabilities import starbase_capabilities
from stars_ai.starbase_planner import (
    _alternate_reality_habitat_gain,
    _preferred_support_design,
    plan_support_base_builds,
)
from stars_ai.planet_economy import decode_race_economy
from stars_ai.persona import BalancedPersona
from stars_ai.strategy.economy import add_economic_orders


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

    # A client starbase refit seeds its new slot from a populated same-hull
    # design, then commits the desired fitting through the 7n record.
    staging, final = create_starbase_design_blocks(
        plan.encoded, player_id=1, baseline_design=plan.encoded,
    )
    parsed_staging = parse_design_change_payload(staging.data)
    parsed_final = parse_design_change_payload(final.data)
    assert staging.data[:2] == bytes.fromhex("01 B1")
    assert final.data[:2] == bytes.fromhex("01 71")
    assert parsed_staging.is_starbase and parsed_final.is_starbase
    assert parsed_final.design_slot == 1
    assert parsed_final.hull_id == 34
    assert tuple(parsed_staging.slots) == plan.encoded.slots
    assert tuple(parsed_final.slots) == plan.encoded.slots


def test_isb_prefers_a_custom_space_dock_when_no_support_design_exists():
    state = _state(isb=True)
    proposal = next(proposal for proposal in plan_design_development(state) if proposal.role == "starbase")

    assert proposal.desired_hull_id == 33
    plan = synthesize_generic_design_proposal(state, proposal.to_payload())
    assert plan is not None
    assert plan.encoded.is_starbase is True
    assert plan.encoded.hull_id == 33


def test_defensive_starbase_uses_full_researched_weapon_and_protection_capacity():
    state = _state()
    payload = {
        "role": "starbase", "name": "Defensive Space Station", "is_starbase": True,
        "desired_hull_id": 34, "desired_hull_name": "Space Station", "priority": 190,
        "purpose": "defense",
    }

    plan = synthesize_generic_design_proposal(state, payload)

    assert plan is not None
    assert plan.encoded.hull_id == 34
    assert sum(slot.count for slot in plan.encoded.slots if slot.category in (16, 32)) > 10
    assert all(
        slot.count == 0 or slot.count >= 1
        for slot in plan.encoded.slots
    )
    assert validate_design(plan.encoded.hull_id, plan.encoded.slots).ok


def test_defensive_support_selection_uses_battle_board_offensive_rating():
    state = _state()
    capabilities = starbase_capabilities(34)
    state.native["starbase_profiles"].extend([
        {
            "design_number": 1, "name": "Laser Screen", "hull_id": 34,
            "hull_name": "Space Station", "capabilities": capabilities,
            "slots": [{"category": 16, "item_id": 0, "count": 12}],
        },
        {
            "design_number": 2, "name": "Heavy Battery", "hull_id": 34,
            "hull_name": "Space Station", "capabilities": capabilities,
            "slots": [{"category": 16, "item_id": 10, "count": 1}],
        },
    ])

    preferred = _preferred_support_design(state, prefer_defensive=True)

    # The old proxy picked twelve low-end lasers by count.  The defense planner
    # must see the one Bludgeon's much stronger actual battle-board offense.
    assert preferred is not None
    assert preferred["name"] == "Heavy Battery"


def test_opening_does_not_spend_the_design_slot_on_a_defense_station():
    state = _state()
    state.year = 2412
    state.fleets.append(Fleet(9, "Foreign Convoy", 2, Position(80, 0), role="combat", combat_power=40))

    proposals = [
        proposal for proposal in plan_design_development(state)
        if proposal.role == "starbase" and proposal.purpose == "defense"
    ]

    assert proposals == []


def test_territorial_intrusion_creates_a_defensive_variant_of_the_existing_support_hull():
    state = _state()
    state.year = 2451
    state.planets[0].native["starbase_capabilities"] = starbase_capabilities(34)
    state.native["starbase_profiles"].append({
        "design_number": 1, "name": "Fleet Support Space Station", "hull_id": 34,
        "hull_name": "Space Station", "capabilities": starbase_capabilities(34), "slots": [],
    })
    state.fleets.append(Fleet(9, "Foreign Convoy", 2, Position(80, 0), role="combat", combat_power=40))

    proposal = next(
        proposal for proposal in plan_design_development(state)
        if proposal.role == "starbase" and proposal.purpose == "defense"
    )

    assert proposal.desired_hull_id == 34


def test_threat_first_designs_an_armed_variant_of_the_current_base_hull():
    state = _state()
    state.year = 2451
    state.tech.construction = 12
    state.planets[0].native.update({
        "has_starbase": True,
        "starbase_design": 0,
        "starbase_capabilities": starbase_capabilities(34),
    })
    state.native["starbase_profiles"][0].update({
        "hull_id": 34, "hull_name": "Space Station",
        "capabilities": starbase_capabilities(34),
    })
    state.fleets.append(Fleet(9, "Foreign Convoy", 2, Position(80, 0), role="combat", combat_power=40))

    immediate = next(
        proposal for proposal in plan_design_development(state)
        if proposal.role == "starbase" and proposal.name.startswith("Immediate")
    )

    assert immediate.desired_hull_id == 34
    assert immediate.purpose == "defense"
    assert "existing base remains active" in immediate.reason


def test_defensive_base_replacement_keeps_existing_orbital_cover_until_completion():
    state = _state()
    state.planets[0].native.update({
        "has_starbase": True,
        "starbase_design": 0,
        "starbase_capabilities": starbase_capabilities(34),
    })
    state.native["starbase_profiles"][0].update({
        "hull_id": 34, "hull_name": "Space Station",
        "capabilities": starbase_capabilities(34), "slots": [],
    })
    state.native["starbase_profiles"].append({
        "design_number": 1, "name": "Armed Space Station", "hull_id": 34,
        "hull_name": "Space Station", "capabilities": starbase_capabilities(34),
        "slots": [{"category": 16, "item_id": 1, "count": 12}],
    })
    state.fleets.append(Fleet(9, "Foreign Convoy", 2, Position(80, 0), role="combat", combat_power=40))

    request = next(request for request in plan_support_base_builds(state) if request.design_slot == 1)

    assert request.planet_id == 0
    assert "retain the installed base as orbital cover" in request.reason
    assert "protection gained per construction turn" in request.reason


def test_researched_weapon_upgrade_creates_and_queues_a_new_station_design_over_starter_weapons():
    state = _state()
    state.year = 2451
    state.tech.weapons = 18
    state.tech.construction = 6
    starter_slots = [
        {"category": 16, "item_id": 0, "count": 8},
        {"category": 4, "item_id": 0, "count": 8},
    ]
    state.planets[0].native.update({
        "has_starbase": True,
        "starbase_design": 0,
        "starbase_capabilities": starbase_capabilities(34),
    })
    state.native["designs"][0].update({"hull_id": 34, "slots": starter_slots})
    state.native["starbase_profiles"][0].update({
        "hull_id": 34,
        "hull_name": "Space Station",
        "capabilities": starbase_capabilities(34),
        "slots": starter_slots,
    })

    proposal = next(
        proposal for proposal in plan_design_development(state)
        if proposal.role == "starbase" and proposal.name.startswith("Modernized")
    )
    design = synthesize_generic_design_proposal(state, proposal.to_payload()).encoded

    assert design.hull_id == 34
    torpedo_slots=[slot for slot in design.slots if slot.category == 32 and slot.count > 0]
    assert len(torpedo_slots) >= 2
    assert any(slot.category in (16, 32) and slot.count > 0 for slot in design.slots)
    assert not any(slot.category == 0x0200 and slot.count > 0 for slot in design.slots)

    # Simulate the mandatory next-M read-back, then verify the normal economy
    # path promotes this design into an overlapping replacement queue.
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

    request = next(
        request for request in plan_support_base_builds(state)
        if request.design_slot == design.slot
    )
    assert request.planet_id in {0, 1}

    orders = OrderSet(state.game_name, state.year, state.player_id)
    add_economic_orders(state, orders, BalancedPersona().build_plan(state))
    queued = [
        order.payload["planet_id"]
        for order in orders.orders
        if order.kind == "set_planet_queue"
        and any(
            item.get("item") == "starbase_design" and item.get("design_slot") == design.slot
            for item in order.payload["queue"]
        )
    ]
    assert queued


def test_alternate_reality_larger_base_adds_habitat_economic_value():
    state = _state()
    state.race.primary_trait = "Alternate Reality"
    home = state.planets[0]
    home.population = 900_000

    value, detail = _alternate_reality_habitat_gain(
        home, 34, 35, decode_race_economy(state.race),
    )

    assert value > 0
    assert detail["current_capacity"] == 1_000_000
    assert detail["target_capacity"] == 2_000_000
    assert detail["added_capacity"] == 1_000_000


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


def test_fortress_station_starts_with_a_partial_weapon_and_shield_fit():
    state = _state()
    state.year = 2445
    state.tech.weapons = 10
    state.tech.propulsion = 2
    plan = synthesize_generic_design_proposal(state, {
        "role": "starbase", "name": "Thin Station", "is_starbase": True,
        "desired_hull_id": 34, "purpose": "defense", "priority": 190,
    })
    assert plan is not None
    weapons = [slot for slot in plan.encoded.slots if slot.category == 32 and slot.count > 0]
    shields = [slot for slot in plan.encoded.slots if slot.category == 4 and slot.count > 0]
    assert weapons
    assert all(slot.count == 4 for slot in weapons)
    assert shields
    assert all(slot.count == 8 for slot in shields)


def test_jihad_starbase_uses_only_missiles_and_fills_orbital_slots_with_electronics():
    state = _state()
    state.tech.weapons = 12
    state.tech.propulsion = 6
    state.tech.electronics = 8
    plan = synthesize_generic_design_proposal(state, {
        "role": "starbase", "name": "Missile Station", "is_starbase": True,
        "desired_hull_id": 34, "purpose": "defense", "priority": 190,
    })
    assert plan is not None
    weapons = [slot for slot in plan.encoded.slots if slot.category in (16, 32) and slot.count > 0]
    assert weapons
    assert all(slot.category == 32 and "missile" in slot_name(slot) for slot in weapons)
    assert not any(slot.category == 0x0200 and slot.count > 0 for slot in plan.encoded.slots)
    assert any(slot.category == 0x0800 and slot.count > 0 for slot in plan.encoded.slots)


def slot_name(slot):
    from stars_ai.starsapi_items import component_spec
    return component_spec(slot.category, slot.item_id).name.casefold()


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
