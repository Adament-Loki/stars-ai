from stars_ai.models import GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.native.x_writer import _encode_queue_item
from stars_ai.planetary_scanners import (
    best_penetrating_planetary_scanner,
    deployed_planetary_sensor_network,
    planetary_scanner_sites,
)
from stars_ai.strategy.economy import add_economic_orders
from stars_ai.scout_policy import custom_scout_missions


def _state(*, nas: bool = False):
    return GameState(
        "sensors", 2425, 1,
        RaceProfile(native={"lrts": ["NAS"] if nas else []}),
        Tech(energy=3, electronics=10, biotechnology=3),
        [
            Planet(0, "Home", Position(0, 0), owner=1, observed=True,
                   population=200_000, native={"is_homeworld": True}),
            Planet(1, "Frontier", Position(120, 0), owner=1, observed=True,
                   population=80_000, native={"strategic_value": 0.9}),
            Planet(2, "Foreign", Position(400, 0), owner=2, observed=True),
        ],
        [],
    )


def test_researched_snooper_is_selected_for_planetary_intelligence():
    capability = best_penetrating_planetary_scanner(_state())
    assert capability is not None
    assert capability["name"] == "Snooper 320X"
    assert capability["range"] == 320
    assert planetary_scanner_sites(_state(), capability) == [1, 0]


def test_nas_race_is_not_offered_a_penetrating_planetary_scanner():
    assert best_penetrating_planetary_scanner(_state(nas=True)) is None


def test_planetary_scanner_uses_standard_production_item_27():
    encoded = _encode_queue_item("planetary_scanner", 1)
    assert encoded == (27 << 10 | 1).to_bytes(2, "little") + (2).to_bytes(2, "little")


def test_live_edge_sensor_covers_multiple_worlds_and_a_penetrating_inner_zone():
    state = _state()
    state.planets[1].native["has_scanner"] = True
    capability = best_penetrating_planetary_scanner(state)
    network = deployed_planetary_sensor_network(state, capability)

    assert network["site_planet_ids"] == [1]
    assert set(network["normal_covered_planet_ids"]) == {0, 1, 2}
    assert set(network["penetrating_covered_planet_ids"]) == {0, 1}


def test_economy_plans_researched_sensor_as_a_standard_queue_item():
    state = _state()
    orders = OrderSet(state.game_name, state.year, state.player_id)

    add_economic_orders(state, orders)

    scanner_orders = [
        order
        for order in orders.orders
        if order.kind == "set_planet_queue"
        and any(item.get("item") == "planetary_scanner" for item in order.payload["queue"])
    ]
    assert {int(order.payload["planet_id"]) for order in scanner_orders} == {0, 1}
    assert state.native["planetary_sensor_plan"]["capability"]["name"] == "Snooper 320X"


def test_sensor_covered_border_gap_does_not_consume_a_scout_but_distant_gap_does():
    state = _state()
    state.planets[2].position = Position(650, 0)
    state.planets.append(Planet(3, "Border Gap", Position(600, 0), observed=False))

    # The nearest owned system is 480 ly away: this verifies border intelligence
    # has no inherited 300-ly territorial veto.
    assert [mission["target_planet_id"] for mission in custom_scout_missions(state)] == [3]

    # An established edge Snooper covers the same gap continuously, so the
    # scout can be reassigned to an uncovered frontier/contested world.
    state.planets[1].position = Position(500, 0)
    state.planets[1].native["has_scanner"] = True
    assert custom_scout_missions(state) == []
