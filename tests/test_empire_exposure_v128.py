from stars_ai.models import Fleet, GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.memory import AgentMemory
from stars_ai.objective_production import plan_objective_ship_builds
from stars_ai.persona import BalancedPersona
from stars_ai.strategy.economy import add_economic_orders
from stars_ai.territorial_defense import assess_territorial_defense


def _designs():
    return [
        {
            "design_number": 1, "name": "Border Escort", "role": "combat",
            "hull_id": 6, "dry_mass": 70, "fuel_capacity": 300,
            "engine_id": 2, "turn_designed": 1,
        },
        {
            "design_number": 2, "name": "Border Miner", "role": "minelayer",
            "hull_id": 5, "dry_mass": 55, "fuel_capacity": 250,
            "engine_id": 2, "turn_designed": 1,
        },
        {
            "design_number": 3, "name": "Capture Carrier", "role": "freighter",
            "hull_id": 11, "dry_mass": 100, "fuel_capacity": 600,
            "cargo_capacity": 1000, "engine_id": 2, "turn_designed": 1,
        },
    ]


def _exposed_state(contact_count: int = 10) -> GameState:
    worlds=[
        Planet(index, f"Border {index}", Position(index * 80, 0), owner=1,
               observed=True, habitability=100, population=200_000)
        for index in range(4)
    ]
    contacts=[
        Fleet(
            50 + index, f"Intruder {index}", 2,
            Position((index % 4) * 80 + 20, 0), role="combat", combat_power=120,
        )
        for index in range(contact_count)
    ]
    return GameState(
        "exposure", 2435, 1, RaceProfile(), Tech(weapons=8, construction=6), worlds, contacts,
        native={"objects": [], "design_profiles": _designs(), "production_by_planet": {}},
    )


def test_many_violations_and_uncovered_anchors_materially_raise_defense_targets():
    quiet = _exposed_state(0)
    pressured = _exposed_state(10)
    quiet_plan = BalancedPersona().build_plan(quiet)
    pressured_plan = BalancedPersona().build_plan(pressured)

    quiet_exposure = assess_territorial_defense(quiet, quiet_plan).exposure
    assessment = assess_territorial_defense(pressured, pressured_plan)
    exposure = assessment.exposure
    builds = plan_objective_ship_builds(pressured, pressured_plan)

    assert exposure.enemy_contacts_near_border >= 10
    assert exposure.uncovered_minefield_anchors >= 3
    assert exposure.combat_reserve_target >= quiet_exposure.combat_reserve_target + 16
    assert exposure.minelayer_force_target >= 5
    assert any(build.role == "combat" and build.priority >= 190 for build in builds)
    assert any(build.role == "minelayer" and build.priority >= 184 for build in builds)


def test_recent_loss_prioritizes_defense_and_counter_capture_ahead_of_factories():
    home = Planet(
        0, "Home", Position(0, 0), owner=1, observed=True, habitability=100,
        population=500_000, factories=0, mines=0, ironium=2_000, boranium=2_000, germanium=2_000,
        native={"is_homeworld": True, "starbase_capabilities": {"can_build_ships": True, "can_refuel": True}},
    )
    lost = Planet(
        1, "Lost Border", Position(80, 0), owner=2, observed=True, habitability=70,
        population=20_000, native={"recently_lost_by_player": True},
    )
    state = GameState(
        "loss", 2435, 1, RaceProfile(), Tech(weapons=4, construction=6), [home, lost], [],
        native={"objects": [], "design_profiles": _designs(), "production_by_planet": {}},
    )
    plan = BalancedPersona().build_plan(state)
    builds = plan_objective_ship_builds(state, plan)
    orders = OrderSet(state.game_name, state.year, state.player_id)

    add_economic_orders(state, orders, plan)

    exposure = state.native["empire_exposure"]
    assert exposure["recent_planet_losses"] == 1
    assert exposure["loss_emergency_turns_remaining"] == 5
    assert any(build.role == "combat" and build.priority >= 190 for build in builds)
    assert any(build.role == "freighter" and "counter-capture" in build.reason for build in builds)
    queue = next(order.payload["queue"] for order in orders.orders if order.kind == "set_planet_queue")
    factory_index = next(index for index, item in enumerate(queue) if item["item"] == "factory")
    defensive_indices = [
        index for index, item in enumerate(queue)
        if item.get("role") == "combat" or "counter-capture" in str(item.get("strategic_reason", ""))
    ]
    assert defensive_indices and min(defensive_indices) < factory_index


def test_exposure_reserve_decays_gradually_after_contacts_clear():
    state = _exposed_state(10)
    first = assess_territorial_defense(state, BalancedPersona().build_plan(state)).exposure
    next_state = _exposed_state(0)
    next_state.year = 2436
    next_state.native["territorial_exposure_history"] = {
        "pressure_reserve": first.sustained_pressure,
        "year": 2435,
        "loss_events": [],
    }
    second = assess_territorial_defense(next_state, BalancedPersona().build_plan(next_state)).exposure

    assert first.combat_reserve_target >= 20
    assert 0 < second.combat_reserve_target < first.combat_reserve_target
    assert second.sustained_pressure < first.sustained_pressure


def test_recent_loss_emergency_persists_and_tapers_through_memory():
    memory = AgentMemory()
    state = _exposed_state(0)
    memory.prepare_territorial_exposure(state, [91])
    first = assess_territorial_defense(state, BalancedPersona().build_plan(state)).exposure
    memory.commit_territorial_exposure(state)

    next_state = _exposed_state(0)
    next_state.year = 2436
    memory.prepare_territorial_exposure(next_state, [])
    second = assess_territorial_defense(next_state, BalancedPersona().build_plan(next_state)).exposure

    assert first.recent_planet_losses == second.recent_planet_losses == 1
    assert first.loss_emergency_turns_remaining == 5
    assert second.loss_emergency_turns_remaining == 4
    assert 1.0 < second.loss_emergency_multiplier < first.loss_emergency_multiplier
