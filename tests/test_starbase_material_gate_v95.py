from stars_ai.models import OrderSet
from stars_ai.starbase_planner import (
    plan_support_base_builds,
    plan_support_base_material_demands,
)
from stars_ai.strategy.economy import add_economic_orders
from tests.test_starbase_network_v83 import _state


def test_unfunded_support_base_is_visible_but_does_not_block_development_queue():
    state = _state(2425)
    candidates = plan_support_base_builds(state)
    assert candidates

    demands = plan_support_base_material_demands(state)
    requested_ids = {request.planet_id for request in candidates}
    blocked = [demand for demand in demands if demand.planet_id in requested_ids and not demand.ready]
    assert blocked
    assert any(sum(demand.mineral_deficit.values()) > 0 for demand in blocked)

    orders = OrderSet(state.game_name, state.year, state.player_id)
    add_economic_orders(state, orders)
    queues = {
        int(order.payload["planet_id"]): order.payload["queue"]
        for order in orders.orders if order.kind == "set_planet_queue"
    }
    for demand in blocked:
        assert not any(item.get("item") == "starbase_design" for item in queues[demand.planet_id])
        assert queues[demand.planet_id]  # mines/factories continue while freight funds the bill
