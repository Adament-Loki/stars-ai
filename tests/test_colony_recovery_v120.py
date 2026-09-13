from stars_ai.fleet_intent import ensure_fleet_activity
from stars_ai.models import Fleet, GameState, OrderSet, Planet, Position, RaceProfile, Tech


def _fuel_profile(capacity):
    return {
        "fuel": 1,
        "effective_fuel": 1,
        "fuel_capacity": capacity,
        "cargo_mass": 0,
        "groups": [{"engine_id": 4, "mass": 50}],
    }


def test_co_located_high_fuel_tender_merges_with_range_blocked_colony_fleet():
    home = Planet(
        0, "Home", Position(0, 0), owner=1, population=100_000,
        native={"starbase_capabilities": {"can_refuel": True}},
    )
    target = Planet(1, "Frontier", Position(100, 0), habitability=80, observed=True)
    colony = Fleet(
        4, "Stranded Colony", 1, Position(50, 0), role="colony",
        cargo_population=2_500,
        native={
            "position_object_id": -1,
            "fuel_profile": _fuel_profile(100),
            "cargo": {"ironium": 0, "boranium": 0, "germanium": 0, "population": 25},
        },
    )
    tender = Fleet(
        5, "Tank Tender", 1, Position(50, 0), role="freighter",
        native={
            "position_object_id": -1,
            "fuel_profile": _fuel_profile(900),
            "cargo": {"ironium": 0, "boranium": 0, "germanium": 0, "population": 0},
        },
    )
    state = GameState(
        "recovery", 2430, 1, RaceProfile(), Tech(), [home, target], [colony, tender],
        native={"colony_range_blocked_fleet_ids": [4]},
    )
    orders = OrderSet(state.game_name, state.year, state.player_id)

    intents = ensure_fleet_activity(state, orders)

    merge = next(order for order in orders.orders if order.kind == "merge_fleets")
    assert merge.payload["target_fleet_id"] == 5
    assert merge.payload["source_fleet_ids"] == [4]
    assert merge.payload["mission"] == "fuel_rescue_merge"
    assert next(intent for intent in intents if intent["fleet_id"] == 4)["action"] == "MERGE FOR FLEET RECOVERY"


def test_idle_colony_hull_reports_an_inflight_claim_as_reserved_not_ready():
    home = Planet(0, "Home", Position(0, 0), owner=1, population=100_000)
    target = Planet(1, "Frontier", Position(20, 0), habitability=80, observed=True)
    idle = Fleet(4, "Idle Colony", 1, Position(0, 0), role="colony", speed=7)
    committed = Fleet(
        5,
        "Committed Colony",
        1,
        Position(5, 0),
        role="colony",
        speed=7,
        destination_planet_id=1,
    )
    state = GameState(
        "claim-reservation", 2430, 1, RaceProfile(), Tech(),
        [home, target], [idle, committed],
    )
    orders = OrderSet(state.game_name, state.year, state.player_id)

    intents = ensure_fleet_activity(state, orders)

    intent = next(item for item in intents if item["fleet_id"] == 4)
    assert intent["action"] == "HOLD / COLONY CLAIM RESERVED"
    assert "Committed Colony" in intent["reason"]
    assert not any(
        order.kind == "move_fleet" and order.payload.get("fleet_id") == 4
        for order in orders.orders
    )
