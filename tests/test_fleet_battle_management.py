from stars_ai.fleet_management import add_tactical_fleet_management, enforce_scout_split_precedence
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


def test_quiet_world_leaves_separate_armed_fleets_unmerged():
    state = _state()
    orders = OrderSet("battle", 2420, 1)

    decisions = add_tactical_fleet_management(state, orders)

    assert decisions == []
    assert [order.kind for order in orders.orders] == []


def test_enemy_at_the_world_joins_idle_armed_fleets():
    state = _state()
    state.fleets.append(Fleet(
        9, "Raider", 2, Position(20, 20), role="combat", combat_power=40,
        native={"position_object_id": 7, "armed": True},
    ))
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
    order = next(order for order in orders.orders if order.kind == "merge_fleets")
    assert order.payload["target_fleet_id"] == 2
    assert order.payload["source_fleet_ids"] == [1]
    assert order.payload["mission"] == "concentrate_for_battle"
    assert state.native["fleet_battle_management"]["battle_policy"] == {
        "allow_chaff": False,
        "allow_split_dodge": False,
        "allow_minimum_damage_split": False,
        "allow_scrap_reclamation_exploit": False,
    }


def test_quiet_fighter_stack_splits_and_a_threatened_stack_stays_together():
    home = Planet(7, "Aldebaran", Position(20, 20), owner=1, observed=True)
    stack = Fleet(
        4, "Fighters", 1, Position(20, 20), role="combat", combat_power=30,
        native={"position_object_id": 7, "armed": True, "ship_count": [4] + [0] * 15},
    )
    state = GameState(
        "battle", 2420, 1, RaceProfile(), Tech(), [home], [stack],
        native={"design_profiles": [{"design_number": 0, "role": "fighter"}]},
    )
    orders = OrderSet("battle", 2420, 1)

    add_tactical_fleet_management(state, orders)
    enforce_scout_split_precedence(state, orders)

    split = next(order for order in orders.orders if order.kind == "split_fleet")
    assert split.payload["singleton_fleets_required"] == 3
    assert split.payload["design_slot"] == 0

    state.fleets.append(Fleet(
        9, "Raider", 2, Position(20, 20), role="combat",
        native={"position_object_id": 7, "armed": True},
    ))
    threatened = OrderSet("battle", 2420, 1)
    add_tactical_fleet_management(state, threatened)
    assert [order.kind for order in threatened.orders] == []


def test_four_privateers_split_ahead_of_one_shared_route():
    home = Planet(7, "Sherman", Position(20, 20), owner=1, observed=True)
    stack = Fleet(
        23, "Onion Privateer", 1, Position(20, 20), role="freighter",
        native={"position_object_id": 7, "ship_count": [0] * 6 + [4] + [0] * 9},
    )
    state = GameState(
        "cargo", 2431, 1, RaceProfile(), Tech(), [home], [stack],
        native={"design_profiles": [{"design_number": 6, "role": "freighter"}]},
    )
    orders = OrderSet("cargo", 2431, 1)
    orders.add(
        "transport_population",
        {"fleet_id": 23, "destination_planet_id": 7, "population_kt": 25},
        "one lane for the whole stack",
    )

    enforce_scout_split_precedence(state, orders)

    split = next(order for order in orders.orders if order.kind == "split_fleet")
    assert split.payload["design_slot"] == 6
    assert split.payload["singleton_fleets_required"] == 3
    assert not [order for order in orders.orders if order.kind == "transport_population"]


def test_loaded_cargo_stack_is_not_split():
    home = Planet(7, "Sherman", Position(20, 20), owner=1, observed=True)
    stack = Fleet(
        12, "Colonizer", 1, Position(20, 20), role="colony", cargo_population=12700,
        native={"position_object_id": 7, "ship_count": [0, 0, 2] + [0] * 13, "cargo": {"population": 127}},
    )
    state = GameState(
        "cargo", 2431, 1, RaceProfile(), Tech(), [home], [stack],
        native={"design_profiles": [{"design_number": 2, "role": "colony"}]},
    )
    orders = OrderSet("cargo", 2431, 1)

    enforce_scout_split_precedence(state, orders)

    assert [order.kind for order in orders.orders] == []


def test_does_not_merge_a_fleet_already_reserved_by_military():
    state = _state()
    orders = OrderSet("battle", 2420, 1)
    orders.add("move_fleet", {"fleet_id": 1, "destination_planet_id": 7}, "already defending")

    decisions = add_tactical_fleet_management(state, orders)

    assert decisions == []
    assert [order.kind for order in orders.orders] == ["move_fleet"]
