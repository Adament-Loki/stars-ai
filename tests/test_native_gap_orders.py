"""Planner and byte checks for host-proven gap orders. No Stars! process."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from stars_ai.models import Fleet, GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.native.x_writer import _battle_plan_block, _rename_fleet_block, _repeat_orders_block
from stars_ai.native_gap_orders import add_native_gap_orders
from stars_ai.strategy.diplomacy import add_diplomacy_orders


class _State:
    player_id = 1


def _state() -> GameState:
    return GameState(
        game_name="gap",
        year=2400,
        player_id=1,
        race=RaceProfile(native={"player_relations": [0, 0, 0]}),
        tech=Tech(),
        planets=[
            Planet(1, "Home", Position(0, 0), owner=1, population=10000, native={"has_starbase": True, "starbase_capabilities": {"can_refuel": True}}),
            Planet(2, "Next", Position(20, 0), owner=1, population=1000, native={"has_starbase": True}),
            Planet(3, "Them", Position(30, 0), owner=3, population=5000),
        ],
        fleets=[
            Fleet(1, "Haul A", 1, Position(0, 0), role="freighter", native={"waypoint_count": 1, "position_object_id": 1, "cargo": {}}),
            Fleet(2, "Haul B", 1, Position(0, 0), role="freighter", native={"waypoint_count": 1, "position_object_id": 1, "cargo": {}}),
            Fleet(3, "Guard", 1, Position(0, 0), role="combat", native={"waypoint_count": 1, "position_object_id": 1, "battle_plan": 2, "cargo": {}}),
            Fleet(4, "Strike", 1, Position(0, 0), role="bomber", native={"waypoint_count": 1, "position_object_id": 1, "cargo": {}}),
        ],
    )


class NativeGapOrderTests(unittest.TestCase):
    def test_battle_plan_and_repeat_bytes(self):
        plan = _battle_plan_block(_State(), 3, 1)
        repeat = _repeat_orders_block(_State(), 3, enabled=True)
        self.assertEqual(plan.type_id, 42)
        self.assertEqual(plan.data, bytes.fromhex("03 00 01 00"))
        self.assertEqual(repeat.type_id, 10)
        self.assertEqual(repeat.data, bytes.fromhex("03 00 01 00"))
        rename = _rename_fleet_block(_State(), 3, "Gap")
        self.assertEqual(rename.type_id, 44)
        self.assertEqual(rename.data, bytes.fromhex("03 00 02 00 00 47 61 70 00"))

    def test_hostile_ai_is_marked_enemy_and_a_human_is_not(self):
        state = _state()
        orders = OrderSet("gap", 2400, 1)
        plan = SimpleNamespace(diplomacy={
            2: {
                "attitude": "hostile",
                "is_human": True,
                "can_ally": False,
                "native_relation": 0,
                "conflict": {"recommended_action": "oppose"},
            },
            3: {
                "attitude": "hostile",
                "is_human": False,
                "can_ally": False,
                "native_relation": 0,
                "conflict": {"recommended_action": "oppose"},
            },
        })
        add_diplomacy_orders(state, orders, plan)
        enemy = [order for order in orders.orders if order.payload.get("relation") == "enemy"]
        self.assertEqual([order.payload["player_id"] for order in enemy], [3])

    def test_gap_planner_scraps_patrols_resets_plan_and_bombs(self):
        state = _state()
        orders = OrderSet("gap", 2400, 1)
        plan = SimpleNamespace(diplomacy={
            3: {"attitude": "hostile", "is_human": False},
        })
        add_native_gap_orders(state, orders, plan)
        kinds = [order.kind for order in orders.orders]
        self.assertIn("scrap_fleet", kinds)
        self.assertIn("set_battle_plan", kinds)
        self.assertIn("move_fleet", kinds)
        self.assertIn("repeat_fleet_orders", kinds)
        self.assertIn("bomb_orbit", kinds)
        self.assertIn("rename_fleet", kinds)
        patrol = next(order for order in orders.orders if order.kind == "move_fleet")
        self.assertEqual(patrol.payload["mission"], "patrol")
        self.assertEqual(patrol.payload["destination_planet_id"], 2)
        scrap = next(order for order in orders.orders if order.kind == "scrap_fleet")
        self.assertEqual(scrap.payload["fleet_id"], 2)


if __name__ == "__main__":
    unittest.main()
