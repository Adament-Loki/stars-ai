from stars_ai.design_development import add_design_development_orders
from stars_ai.models import Fleet, GameState, OrderSet, Position, RaceProfile, Tech


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
