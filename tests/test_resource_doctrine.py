from stars_ai.expansion_network import evaluate_expansion_network
from stars_ai.core.memory import AgentMemory
from stars_ai.invasion import empire_ready_to_press_border, worth_securing
from stars_ai.models import Fleet, GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.persona import BalancedPersona
from stars_ai.planet_economy import measure_empire_resources
from stars_ai.population_invasion import population_capture_targets
from stars_ai.strategy.military import add_military_orders


def _world(**kwargs):
    defaults = dict(owner=1, observed=True, habitability=80, population=100_000, factories=20)
    defaults.update(kwargs)
    return Planet(defaults.pop("id"), defaults.pop("name"), defaults.pop("position"), **defaults)


def test_empire_resources_include_population_and_operating_factories():
    state = GameState(
        "resources", 2420, 1, RaceProfile(), Tech(),
        [_world(id=0, name="Home", position=Position(0, 0), population=100_000, factories=50)],
        [],
    )

    report = measure_empire_resources(state)

    assert report["population_resources"] == 100
    assert report["factory_resources"] == 50
    assert report["resources"] == 150
    assert report["resources_at_factory_cap"] >= report["resources"]


def test_memory_remembers_the_resource_delta():
    state = GameState(
        "resources", 2420, 1, RaceProfile(), Tech(),
        [_world(id=0, name="Home", position=Position(0, 0), population=100_000, factories=50)],
        [],
        native={},
    )
    memory = AgentMemory()
    memory.prepare_empire_resources(state)
    memory.commit_empire_resources(state)
    state.planets[0].factories = 80
    state.year = 2421

    report = memory.prepare_empire_resources(state)

    assert report["previous_resources"] == 150
    assert report["resources"] > 150
    assert report["resource_delta"] == report["resources"] - 150


def test_low_value_enemy_world_is_not_a_capture_target():
    state = GameState(
        "raid", 2440, 1, RaceProfile(), Tech(),
        [
            _world(id=0, name="Home", position=Position(0, 0)),
            Planet(2, "Rock", Position(40, 0), owner=2, observed=True, habitability=30, population=20_000),
        ],
        [],
    )

    assert population_capture_targets(state) == []
    assert not worth_securing(state.planets[1])


def test_saturated_border_raids_a_low_value_world_and_does_not_secure_it():
    worlds = [
        _world(id=index, name=f"Core{index}", position=Position(index * 30, 0), population=200_000, factories=200)
        for index in range(8)
    ]
    worlds.append(Planet(20, "Outpost", Position(80, 0), owner=2, observed=True, habitability=25, population=30_000, defenses=0))
    state = GameState(
        "saturated", 2455, 1, RaceProfile(), Tech(), worlds,
        [
            Fleet(1, "Guard", 1, Position(0, 0), role="combat", combat_power=80),
            Fleet(2, "Raider", 1, Position(60, 0), role="combat", combat_power=80),
        ],
        native={},
    )
    plan = BalancedPersona().build_plan(state)
    plan.phase_assessment["expansion_saturation"] = 0.8
    orders = OrderSet("saturated", 2455, 1)

    assert empire_ready_to_press_border(state, plan)
    add_military_orders(state, orders, plan)

    attack = next(order for order in orders.orders if order.payload.get("invasion_action") == "RAID")
    assert attack.payload["destination_planet_id"] == 20
    assert attack.payload["secure_after_attack"] is False
    assert 20 in state.native["raid_only_planet_ids"]


def test_a_light_scout_pushes_a_high_value_alternate_reality_fort():
    state = GameState(
        "paper", 2425, 1, RaceProfile(), Tech(),
        [
            _world(id=0, name="Home", position=Position(0, 0)),
            Planet(
                9, "Soft Green", Position(50, 0), owner=6, observed=True,
                habitability=90, population=0, defenses=0,
                native={"owner_prt_id": 8, "owner_prt_name": "Alternate Reality",
                        "starbase_capabilities": {"is_orbital_fort": True}},
            ),
            Planet(
                10, "Red Fort", Position(40, 0), owner=6, observed=True,
                habitability=20, population=0, defenses=0,
                native={"owner_prt_id": 8},
            ),
        ],
        [
            Fleet(1, "Battle", 1, Position(0, 0), role="combat", combat_power=400),
            Fleet(2, "Scout", 1, Position(0, 0), role="scout", combat_power=5, native={"weapon_count": 1}),
        ],
    )
    orders = OrderSet("paper", 2425, 1)

    add_military_orders(state, orders, BalancedPersona().build_plan(state))

    attack = next(order for order in orders.orders if order.payload.get("paper_tiger"))
    assert attack.payload["fleet_id"] == 2
    assert attack.payload["destination_planet_id"] == 9
    assert attack.payload["mission"] == "attack"
    assert attack.payload["secure_after_attack"] is True
    assert 9 not in (state.native.get("raid_only_planet_ids") or [])


def test_worlds_outside_the_layer1_cap_still_receive_a_population_parent():
    worlds = [
        Planet(0, "Home", Position(0, 0), owner=1, habitability=100, population=800_000, factories=200,
               native={"is_homeworld": True, "starbase_capabilities": {"can_build_ships": True, "can_refuel": True}}),
    ]
    for index in range(1, 7):
        worlds.append(Planet(
            index, f"Colony{index}", Position(80 + index * 40, 0), owner=1,
            habitability=70, population=8_000, factories=2,
        ))
    state = GameState("feed", 2450, 1, RaceProfile(), Tech(), worlds, [])

    network = evaluate_expansion_network(state)
    fed = [hub for hub in network.hubs if hub.planet_id != 0 and hub.parent_exporter_id is not None]

    assert len(fed) == 6
    assert all(hub.import_population_to_25 > 0 for hub in fed)
