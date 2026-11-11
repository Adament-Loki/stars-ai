from stars_ai.design_development import add_design_development_orders
from stars_ai.models import Fleet, GameState, OrderSet, Position, RaceProfile, Tech
from stars_ai.starsapi_items import ENGINE, MECHANICAL


def test_unbuilt_combat_variant_is_deleted_when_shared_design_is_live():
    state = GameState(
        "inventory", 2449, 1, RaceProfile(), Tech(), [],
        [Fleet(1, "Combat fleet", 1, Position(0, 0), native={"ship_count": [0, 0, 1, 0]})],
        native={
            "designs": [
                {"design_number": 2, "name": "Built Combat", "hull_id": 6, "total_remaining": 0, "turn_designed": 20},
                {"design_number": 3, "name": "Unused Escort", "hull_id": 6, "total_remaining": 0, "turn_designed": 21},
            ],
            "design_profiles": [
                {"design_number": 2, "name": "Built Combat", "role": "combat"},
                {"design_number": 3, "name": "Unused Escort", "role": "combat"},
            ],
            "production_by_planet": {},
        },
    )
    orders = OrderSet(state.game_name, state.year, state.player_id)
    add_design_development_orders(state, orders)
    deletions = [order for order in orders.orders if order.kind == "delete_ship_design"]
    assert len(deletions) == 1
    assert deletions[0].payload["target_slot"] == 3


def test_unused_older_scout_is_deleted_and_the_current_one_stays():
    state = GameState(
        "inventory", 2449, 1, RaceProfile(), Tech(), [], [],
        native={
            "designs": [
                {"design_number": 1, "name": "Old Scout", "hull_id": 4, "total_remaining": 0, "turn_designed": 10},
                {"design_number": 4, "name": "Current Scout", "hull_id": 4, "total_remaining": 0, "turn_designed": 30},
            ],
            "design_profiles": [
                {"design_number": 1, "role": "scout"},
                {"design_number": 4, "role": "scout"},
            ],
            "production_by_planet": {},
        },
    )
    orders = OrderSet(state.game_name, state.year, state.player_id)
    add_design_development_orders(state, orders)
    deletions = [order for order in orders.orders if order.kind == "delete_ship_design"]
    assert len(deletions) == 1
    assert deletions[0].payload["target_slot"] == 1


def test_obsolete_uninstalled_starbase_is_deleted():
    state = GameState(
        "inventory", 2449, 1, RaceProfile(), Tech(), [], [],
        native={
            "designs": [
                {
                    "design_number": 0, "name": "Starbase", "hull_id": 34, "is_starbase": True,
                    "total_remaining": 0, "turn_designed": 0,
                    "slots": [{"category": 16, "item_id": 0, "count": 8}],
                },
                {
                    "design_number": 1, "name": "Base 02", "hull_id": 32, "is_starbase": True,
                    "total_remaining": 0, "turn_designed": 40,
                    "slots": [{"category": 32, "item_id": 2, "count": 4}],
                },
            ],
            "design_profiles": [],
            "production_by_planet": {},
        },
    )
    orders = OrderSet(state.game_name, state.year, state.player_id)
    add_design_development_orders(state, orders)
    deletions = [order for order in orders.orders if order.kind == "delete_starbase_design"]
    assert len(deletions) == 1
    assert deletions[0].payload["target_slot"] == 0


def test_installed_starbase_is_not_deleted_even_when_a_better_design_exists():
    home_native = {"has_starbase": True, "starbase_design": 0}
    from stars_ai.models import Planet
    state = GameState(
        "inventory", 2449, 1, RaceProfile(), Tech(),
        [Planet(0, "Home", Position(0, 0), owner=1, native=home_native)],
        [],
        native={
            "designs": [
                {
                    "design_number": 0, "name": "Starbase", "hull_id": 34, "is_starbase": True,
                    "total_remaining": 0, "turn_designed": 0,
                    "slots": [{"category": 16, "item_id": 0, "count": 8}],
                },
                {
                    "design_number": 1, "name": "Base 02", "hull_id": 35, "is_starbase": True,
                    "total_remaining": 0, "turn_designed": 40,
                    "slots": [{"category": 32, "item_id": 2, "count": 16}],
                },
            ],
            "design_profiles": [],
            "production_by_planet": {},
        },
    )
    orders = OrderSet(state.game_name, state.year, state.player_id)
    add_design_development_orders(state, orders)
    assert [order for order in orders.orders if order.kind == "delete_starbase_design"] == []


def test_legacy_non_cargo_colonizer_is_reclaimed_only_after_owner_readback_is_empty():
    state = GameState(
        "inventory", 2449, 1, RaceProfile(), Tech(), [], [],
        native={
            "designs": [
                {
                    "design_number": 2, "name": "Broken Scout Colonizer", "hull_id": 4,
                    "total_remaining": 0, "turn_designed": 20,
                    "slots": [
                        {"category": ENGINE, "item_id": 2, "count": 1},
                        {"category": MECHANICAL, "item_id": 0, "count": 1},
                    ],
                },
                {
                    "design_number": 3, "name": "Working Colonizer", "hull_id": 15,
                    "total_remaining": 2, "turn_designed": 21,
                    "slots": [
                        {"category": ENGINE, "item_id": 2, "count": 1},
                        {"category": MECHANICAL, "item_id": 0, "count": 1},
                    ],
                },
            ],
            "design_profiles": [], "production_by_planet": {},
        },
    )
    orders = OrderSet(state.game_name, state.year, state.player_id)

    add_design_development_orders(state, orders)

    deletions = [order for order in orders.orders if order.kind == "delete_ship_design"]
    assert len(deletions) == 1
    assert deletions[0].payload["target_slot"] == 2
    assert "non-cargo" in deletions[0].reason


def test_idle_alternate_reality_colonizer_on_a_non_colony_ship_is_reclaimed():
    state = GameState(
        "inventory", 2449, 1, RaceProfile(native={"prt_id": 8}), Tech(), [], [],
        native={
            "designs": [{
                "design_number": 2, "name": "Invalid AR Privateer", "hull_id": 11,
                "total_remaining": 0, "turn_designed": 20,
                "slots": [
                    {"category": ENGINE, "item_id": 2, "count": 1},
                    {"category": 0, "item_id": 0, "count": 0},
                    {"category": MECHANICAL, "item_id": 0, "count": 1},
                    {"category": 0, "item_id": 0, "count": 0},
                    {"category": 0, "item_id": 0, "count": 0},
                ],
            }],
            "design_profiles": [], "production_by_planet": {},
        },
    )
    orders = OrderSet(state.game_name, state.year, state.player_id)

    add_design_development_orders(state, orders)

    deletion = next(order for order in orders.orders if order.kind == "delete_ship_design")
    assert deletion.payload["target_slot"] == 2
    assert "Alternate Reality" in deletion.reason
