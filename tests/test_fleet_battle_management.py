from stars_ai.fleet_management import add_tactical_fleet_management
from stars_ai.models import Fleet, GameState, OrderSet, Planet, Position, RaceProfile, Tech


def _state():
    home = Planet(7, "Aldebaran", Position(20, 20), owner=1, observed=True)
    return GameState(
        "battle", 2420, 1, RaceProfile(), Tech(), [home],
        [
            Fleet(1, "Screen A", 1, Position(20, 20), role="combat", combat_power=12,
                  native={"position_object_id": 7, "armed": True}),
            Fleet(2, "Screen B", 1, Position(20, 20), role="escort", combat_power=25,
                  native={"position_object_id": 7, "armed": True}),
            Fleet(3, "Mine Guard", 1, Position(20, 20), role="minelayer", combat_power=10,
                  native={"position_object_id": 7, "armed": True}),
            Fleet(4, "Departing", 1, Position(20, 20), role="combat", combat_power=30,
                  destination_planet_id=7, native={"position_object_id": 7, "armed": True}),
            Fleet(5, "Armed Scout", 1, Position(20, 20), role="scout", combat_power=4,
                  native={"position_object_id": 7, "armed": True}),
        ],
    )


def test_concentrates_only_idle_armed_fleets_at_owned_world():
    state = _state()
    orders = OrderSet("battle", 2420, 1)

    decisions = add_tactical_fleet_management(state, orders)

    assert decisions == [{
        "planet_id": 7,
        "planet_name": "Aldebaran",
        "retained_fleet_id": 2,
        "source_fleet_ids": [1],
        "fleet_count": 2,
        "estimated_combined_offensive_value": 37.0,
    }]
    assert len(orders.orders) == 1
    order = orders.orders[0]
    assert order.kind == "merge_fleets"
    assert order.payload["target_fleet_id"] == 2
    assert order.payload["source_fleet_ids"] == [1]
    assert order.payload["mission"] == "concentrate_for_battle"
    assert state.native["fleet_battle_management"]["battle_policy"] == {
        "allow_chaff": False,
        "allow_split_dodge": False,
        "allow_minimum_damage_split": False,
        "allow_scrap_reclamation_exploit": False,
    }


def test_does_not_merge_a_fleet_already_reserved_by_military():
    state = _state()
    orders = OrderSet("battle", 2420, 1)
    orders.add("move_fleet", {"fleet_id": 1, "destination_planet_id": 7}, "already defending")

    decisions = add_tactical_fleet_management(state, orders)

    assert decisions == []
    assert [order.kind for order in orders.orders] == ["move_fleet"]
