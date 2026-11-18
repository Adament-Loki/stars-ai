"""Planner and byte checks for host-proven gap orders. No Stars! process."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from stars_ai.models import Fleet, GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.native.design_change import EncodedShipDesign, edit_unused_ship_design_block
from stars_ai.design_legality import ComponentRef
from stars_ai.native.x_writer import (
    _battle_plan_block, _encode_queue_item, _lay_minefield_travel_blocks,
    _move_and_remote_mine_blocks, _planet_fling_block, _planet_ship_route_block,
    _merge_with_fleet_blocks, _player_message_block, _rename_fleet_block, _repeat_orders_block,
    _repeat_transport_blocks, _waypoint_add_block,
)
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

    def test_packet_fling_matches_the_client_route(self):
        data_to_viscous = _planet_fling_block(90, 75, 5)
        down_and_out_to_data = _planet_fling_block(61, 90, 5)
        self.assertEqual(data_to_viscous.data.hex(" "), "5a 00 98 08 00 00")
        self.assertEqual(down_and_out_to_data.data.hex(" "), "3d 00 b6 08 00 00")
        self.assertEqual(_planet_fling_block(90, 75, 6).data.hex(" "), "5a 00 98 10 00 00")
        self.assertEqual(_planet_fling_block(61, 90, 7).data.hex(" "), "3d 00 b6 18 00 00")
        self.assertEqual(_encode_queue_item("ironium_packet", 1).hex(" "), "01 38 02 00")
        self.assertEqual(_encode_queue_item("mixed_packet", 4).hex(" "), "04 44 02 00")

    def test_ship_route_message_and_travel_orders_match_the_client(self):
        self.assertEqual(_planet_ship_route_block(126, 157).data.hex(" "), "7e 00 00 00 4f 00")
        self.assertEqual(_player_message_block(1, "Hello").data.hex(" "), "00 00 00 00 01 00 01 00 00 00 03 00 b7 25 57")
        home = Planet(126, "Hot Tip", Position(1502, 1196), owner=1)
        charity = Planet(128, "Charity", Position(1516, 1130), owner=1)
        columbus = Planet(227, "Columbus", Position(1947, 1482), owner=None)
        miner = Fleet(2, "Miner", 2, Position(1966, 1551), role="miner", native={
            "waypoints": [{"position_object_type": 0x91, "task": 0, "warp": 0}],
        })
        layer = Fleet(1, "Mine Layer", 1, Position(1502, 1196), role="minelayer")
        state = GameState("gap", 2400, 1, RaceProfile(), Tech(), [home, charity, columbus], [layer, miner])
        travel = _lay_minefield_travel_blocks(state, {"fleet_id": 1, "destination_planet_id": 128, "warp": 6})
        self.assertEqual(travel[0].data.hex(" "), "01 00 01 00 ec 05 6a 04 80 00 60 11")
        self.assertEqual(travel[1].data.hex(" "), "01 00 01 00 ec 05 6a 04 80 00 66 11 05")
        state.player_id = 2
        mined = _move_and_remote_mine_blocks(state, {"fleet_id": 2, "destination_planet_id": 227, "warp": 5})
        self.assertEqual(mined[0].data.hex(" "), "02 02 01 00 9b 07 ca 05 e3 00 50 91")
        self.assertEqual(mined[1].data.hex(" "), "02 02 01 00 9b 07 ca 05 e3 00 53 91")
        state.player_id = 1
        shuttle = _repeat_transport_blocks(state, {
            "fleet_id": 6, "load_planet_id": 128, "unload_planet_id": 126, "warp": 9,
        })
        self.assertEqual(shuttle[1].data.hex(" "), "06 00 01 00 ec 05 6a 04 80 00 91 11 00 10")
        self.assertEqual(shuttle[3].data.hex(" "), "06 00 02 00 de 05 ac 04 7e 00 91 11 00 20")
        self.assertEqual(shuttle[4].data.hex(" "), "06 00 01 00")
        ubert = Fleet(17, "Mini Colony", 3, Position(1522, 1403), role="colony")
        patrol = GameState("gap", 2400, 1, RaceProfile(), Tech(), [home], [ubert])
        intercept = _waypoint_add_block(patrol, {
            "fleet_id": 3, "destination_fleet_id": 17, "destination_fleet_owner": 3, "warp": 8,
        }, object_type=0x12)
        self.assertEqual(intercept.data.hex(" "), "03 00 01 00 f2 05 7b 05 11 04 80 12")

    def test_merge_with_fleet_matches_the_examples2_pair(self):
        state = GameState(
            game_name="gap", year=2420, player_id=1,
            race=RaceProfile(native={}), tech=Tech(),
            planets=[Planet(114, "Anchor", Position(1451, 1235), owner=1)],
            fleets=[
                Fleet(6, "Large Freighter", 1, Position(1502, 1196), role="freighter"),
                Fleet(0, "Friend", 1, Position(1451, 1235), role="scout", native={"position_object_id": 114}),
            ],
        )
        blocks = _merge_with_fleet_blocks(state, {"fleet_id": 6, "destination_fleet_id": 0, "warp": 8})
        self.assertEqual(blocks[0].data.hex(" "), "06 00 01 00 ab 05 d3 04 72 00 80 11")
        self.assertEqual(blocks[1].data.hex(" "), "06 00 01 00 ab 05 d3 04 00 00 84 12")

    def test_unused_destroyer_edit_uses_the_single_type27_record(self):
        design = EncodedShipDesign(
            slot=3, hull_id=6, pic=24, armor=275, turn_designed=11,
            slots=(
                ComponentRef(1, 2, 1), ComponentRef(16, 1, 1), ComponentRef(16, 1, 1),
                ComponentRef(16, 1, 1), ComponentRef(8, 1, 2), ComponentRef(4096, 7, 1),
                ComponentRef(2048, 12, 1),
            ),
            name="Destroyer",
        )
        block = edit_unused_ship_design_block(design, player_id=1)
        self.assertEqual(
            block.data.hex(" "),
            "01 63 07 0d 06 18 13 01 07 0b 00 00 00 00 00 00 00 00 00 "
            "01 00 02 01 10 00 01 01 10 00 01 01 10 00 01 01 08 00 01 "
            "02 00 10 07 01 00 08 0c 01 06 b3 29 a8 7e 22 8f",
        )


if __name__ == "__main__":
    unittest.main()
