from stars_ai.ally_intel import ally_turn_path, share_ally_observations
from stars_ai.autohost.models import WindowsAutoHostConfig, expand_alliances
from stars_ai.models import Fleet, GameState, Planet, Position, RaceProfile, Tech
from stars_ai.persona import BalancedPersona
from stars_ai.strategy.military import add_military_orders
from stars_ai.models import OrderSet
from pathlib import Path


def test_alliance_group_becomes_every_friend_pair():
    assert expand_alliances([[1, 2, 3]], []) == [[1, 2], [1, 3], [2, 3]]
    cfg = WindowsAutoHostConfig(
        stars_exe="stars.exe",
        seed_dir="seed",
        output_dir="out",
        allied_groups=[[1, 2, 3, 4]],
        allied_pairs=[[1, 5]],
    )
    assert cfg.allied_pairs == [[1, 2], [1, 3], [1, 4], [2, 3], [2, 4], [3, 4], [1, 5]]


def test_ally_turn_path_uses_the_same_game_basename():
    assert ally_turn_path(Path("GAME.m1"), 4) == Path("GAME.m4")


def test_unobserved_planet_and_foreign_fleet_come_from_the_ally():
    ours = GameState(
        "g", 2401, 1, RaceProfile(), Tech(),
        [
            Planet(1, "Known", Position(0, 0), owner=1, observed=True, habitability=80),
            Planet(2, "Unknown", Position(40, 0), observed=False),
        ],
        [Fleet(1, "Ours", 1, Position(0, 0), role="colony")],
    )
    ally = GameState(
        "g", 2401, 2, RaceProfile(), Tech(),
        [
            Planet(2, "Scouted", Position(40, 0), owner=None, observed=True, habitability=70, population=0),
            Planet(1, "Stale home", Position(0, 0), owner=1, observed=True, habitability=10),
        ],
        [
            Fleet(9, "Enemy", 6, Position(40, 0), role="scout", combat_power=2),
            Fleet(1, "Their copy of us", 1, Position(5, 5), role="colony"),
        ],
    )

    summary = share_ally_observations(ours, [ally])

    scouted = next(planet for planet in ours.planets if planet.id == 2)
    home = next(planet for planet in ours.planets if planet.id == 1)
    assert scouted.observed is True
    assert scouted.habitability == 70
    assert scouted.native["shared_from_ally"] == 2
    assert home.habitability == 80
    assert summary["shared_planets"] == [2]
    assert [fleet.id for fleet in ours.fleets] == [1, 9]
    assert ours.fleets[-1].native["shared_from_ally"] == 2


def test_configured_ally_is_not_attacked_despite_nearby_strength():
    state = GameState(
        "g", 2410, 1,
        RaceProfile(native={"player_relations": [0, 0]}),
        Tech(),
        [Planet(0, "Home", Position(0, 0), owner=1, observed=True)],
        [
            Fleet(0, "Our Combat", 1, Position(0, 0), role="combat", combat_power=10, speed=8),
            Fleet(1, "Ally Fleet", 2, Position(10, 0), role="combat", combat_power=500, speed=8),
        ],
        native={"configured_allies": [2]},
    )
    plan = BalancedPersona().build_plan(state)
    orders = OrderSet("g", 2410, 1)
    add_military_orders(state, orders, plan)
    assert plan.diplomacy[2]["attitude"] == "allied"
    assert not any(order.kind == "move_fleet" for order in orders.orders)
