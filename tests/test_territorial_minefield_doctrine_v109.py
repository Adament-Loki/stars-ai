from stars_ai.design_development import plan_design_development
from stars_ai.adapters.native_core_adapter import _role_for_design
from stars_ai.models import Fleet, GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.native.x_writer import _minefield_task_block, _waypoint_add_block
from stars_ai.objective_production import plan_objective_ship_builds
from stars_ai.persona import BalancedPersona
from stars_ai.ship_design_synth import synthesize_generic_design_proposal
from stars_ai.strategy.military import add_military_orders
from stars_ai.territorial_defense import assess_territorial_defense
from types import SimpleNamespace


def _state(*, friendly: bool = False, scout_only: bool = False) -> GameState:
    home = Planet(0, "Home", Position(0, 0), owner=1, population=200_000)
    frontier = Planet(1, "Frontier", Position(110, 0), owner=1, population=80_000)
    intruder = Fleet(
        9, "Foreign Convoy", 2, Position(80, 0),
        role="scout" if scout_only else "unknown",
        combat_power=15 if scout_only else 30,
        native={"cargo": ({"population": 80} if not scout_only else {})},
    )
    combat = Fleet(1, "Patrol", 1, Position(0, 0), role="combat", combat_power=300)
    minelayer = Fleet(
        2, "Mine Layer", 1, Position(0, 0), role="minelayer", combat_power=40,
        native={
            "position_object_id": 0,
            "waypoint_count": 1,
            "waypoints": [{"position_object": 0, "position_object_type": 0x11, "warp": 0, "task": 0}],
        },
    )
    relations = [0, 1] if friendly else []
    return GameState(
        "territory", 2420, 1, RaceProfile(native={"player_relations": relations, "prt_id": 3}),
        Tech(weapons=6, energy=6, propulsion=4, construction=4),
        [home, frontier], [combat, minelayer, intruder], native={
            "designs": [],
            "design_profiles": [{
                "design_number": 4, "name": "Mine Layer", "role": "minelayer", "hull_id": 27,
                "engine_id": 2, "turn_designed": 10,
            }, {
                "design_number": 5, "name": "Patrol", "role": "combat", "hull_id": 6,
                "engine_id": 2, "turn_designed": 10,
            }],
            "production_by_planet": {},
            "objects": [],
        },
    )


def test_neutral_transport_inside_claimed_space_requests_patrol_and_minefield():
    state = _state()
    plan = BalancedPersona().build_plan(state)

    assessment = assess_territorial_defense(state, plan)
    orders = OrderSet(state.game_name, state.year, state.player_id)
    add_military_orders(state, orders, plan)

    assert len(assessment.violations) == 1
    violation = assessment.violations[0]
    assert violation.classification == "transport"
    assert violation.anchor_planet_id == 1
    assert violation.requires_patrol and violation.requires_minefield
    assert assessment.uncovered_minefield_anchor_ids == (1,)
    assert any(order.kind == "move_fleet" and order.payload.get("mission") == "territorial_intercept" for order in orders.orders)
    assert any(order.kind == "move_fleet" and order.payload.get("mission") == "minefield_deploy" for order in orders.orders)


def test_scout_and_friend_traffic_do_not_trigger_minefield_doctrine():
    scout_state = _state(scout_only=True)
    friendly_state = _state(friendly=True)

    assert not assess_territorial_defense(scout_state, BalancedPersona().build_plan(scout_state)).violations
    assert not assess_territorial_defense(friendly_state, BalancedPersona().build_plan(friendly_state)).violations


def test_patrol_targets_the_violating_fleet_instead_of_an_inferred_origin_or_destination():
    state = _state()
    state.fleets[-1].destination_planet_id = 0
    orders = OrderSet(state.game_name, state.year, state.player_id)

    add_military_orders(state, orders, BalancedPersona().build_plan(state))

    patrol = next(order for order in orders.orders if order.payload.get("mission") == "territorial_intercept")
    assert patrol.payload["destination_fleet_id"] == 9
    assert patrol.payload["destination_fleet_owner"] == 2
    assert "destination_planet_id" not in patrol.payload


def test_direct_fleet_intercept_uses_the_starsapi_target_type_two_waypoint_shape():
    state = _state()

    block = _waypoint_add_block(state, {
        "fleet_id": 1,
        "destination_fleet_id": 9,
        "destination_fleet_owner": 2,
        "warp": 9,
        "mission": "territorial_intercept",
    })

    assert block.type_id == 4
    assert block.data[4:8] == bytes.fromhex("50 00 00 00")
    assert block.data[8:10] == bytes.fromhex("09 00")
    assert block.data[10:] == bytes.fromhex("90 12")


def test_source_world_escalation_is_authorized_only_after_territory_can_be_held():
    state = _state()
    source = Planet(
        3, "Likely Host", Position(170, 0), owner=2, habitability=70,
        population=150_000, defenses=20, native={"strategic_value": 0.8},
    )
    state.planets.append(source)
    plan = BalancedPersona().build_plan(state)

    secure = assess_territorial_defense(state, plan).source_escalation
    assert secure.source_planet_id == 3
    assert secure.can_hold_current_territory
    assert secure.invasion_authorized
    assert secure.status == "PREPARE_SOURCE_INVASION"

    state.fleets[0].combat_power = 100
    constrained = assess_territorial_defense(state, plan).source_escalation
    assert not constrained.can_hold_current_territory
    assert not constrained.desperate_to_neutralize_host
    assert not constrained.invasion_authorized
    assert constrained.status == "DEFEND_TERRITORY"

    orders = OrderSet(state.game_name, state.year, state.player_id)
    add_military_orders(state, orders, plan)
    assert not any(order.payload.get("destination_planet_id") == source.id for order in orders.orders)


def test_stationary_minelayer_uses_the_traced_task_four_transition():
    state = _state()

    block = _minefield_task_block(state, {
        "fleet_id": 2,
        "destination_planet_id": 0,
        "minefield_type": "standard",
    })

    assert block.type_id == 5
    assert block.data[10] & 0x0F == 4


def test_violation_drives_minelayer_build_and_design_need_when_missing():
    state = _state()
    plan = BalancedPersona().build_plan(state)

    builds = plan_objective_ship_builds(state, plan)
    assert any(build.role == "minelayer" for build in builds)

    state.native["design_profiles"] = [
        profile for profile in state.native["design_profiles"] if profile["role"] != "minelayer"
    ]
    proposals = plan_design_development(state, plan)
    minelayer = next(proposal for proposal in proposals if proposal.role == "minelayer")
    # This race has Construction 4: the Destroyer is available before the
    # Frigate's Construction-6 requirement, and both are general-purpose
    # Mine Layer-capable hulls.
    assert minelayer.desired_hull_id == 6
    native_plan = synthesize_generic_design_proposal(state, minelayer.to_payload())
    assert native_plan is not None
    assert native_plan.role == "minelayer"


def test_non_space_demolition_race_uses_a_general_purpose_mine_layer_hull():
    state = _state()
    state.race.native["prt_id"] = 3
    state.native["design_profiles"] = [
        profile for profile in state.native["design_profiles"] if profile["role"] != "minelayer"
    ]
    plan = BalancedPersona().build_plan(state)
    orders = OrderSet(state.game_name, state.year, state.player_id)

    add_military_orders(state, orders, plan)
    proposals = plan_design_development(state, plan)

    assert any(order.payload.get("mission") == "territorial_intercept" for order in orders.orders)
    minelayer = next(proposal for proposal in proposals if proposal.role == "minelayer")
    assert minelayer.desired_hull_id == 6
    native_plan = synthesize_generic_design_proposal(state, minelayer.to_payload())
    assert native_plan is not None
    assert native_plan.encoded.hull_id == 6
    assert any(slot.category == 0x0100 and slot.count > 0 for slot in native_plan.encoded.slots)


def test_minelayer_role_requires_an_actual_mine_layer_component():
    blank = SimpleNamespace(hull_id=5, slots=[SimpleNamespace(category=0, count=0)])
    fitted = SimpleNamespace(hull_id=5, slots=[SimpleNamespace(category=0x100, count=1)])

    assert _role_for_design(blank) == "unknown"
    assert _role_for_design(fitted) == "minelayer"
