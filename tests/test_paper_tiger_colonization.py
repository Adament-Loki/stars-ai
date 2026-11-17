"""Colonize orders must not aim at an occupied world or a starbase.

A paper-tiger claim is an empty Alternate Reality fort. The military path
sends a light armed ship to destroy it. Colonize against that world is
illegal until the world is unowned and the fort is gone.
"""

from stars_ai.adapters.native_core_adapter import NativeCoreTurnAdapter
from stars_ai.colony_planner import (
    colonization_block_reason,
    planet_has_starbase,
    score_colony_candidates,
)
from stars_ai.models import Fleet, GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.native.x_writer import write_native_orders
from stars_ai.persona import BalancedPersona
from stars_ai.strategy.economy import add_economic_orders
from stars_ai.strategy.military import add_military_orders

from tests.helpers.resources import test_resource as resource_path


def _home():
    return Planet(
        0, "Home", Position(0, 0), owner=1, observed=True,
        habitability=100, population=200_000,
    )


def _colony_ship():
    return Fleet(
        3, "Colony", 1, Position(0, 0), role="colony", speed=7,
        native={"position_object_id": 0},
    )


def _fort(**kwargs):
    native = {
        "owner_prt_id": 8,
        "owner_prt_name": "Alternate Reality",
        "starbase_capabilities": {"is_orbital_fort": True},
    }
    native.update(kwargs.pop("native", {}))
    defaults = dict(
        owner=6, observed=True, habitability=90, population=0, defenses=0, native=native,
    )
    defaults.update(kwargs)
    return Planet(9, "Soft Green", Position(50, 0), **defaults)


def _state(planets, fleets, **native):
    return GameState(
        "paper", 2425, 1, RaceProfile(), Tech(),
        planets, fleets, native=native,
    )


def _colonize_orders(orders):
    return [
        order for order in orders.orders
        if order.kind == "colony_operation" or order.payload.get("mission") == "colonize"
    ]


def test_paper_tiger_claim_is_attacked_and_not_colonized():
    fort = _fort()
    empty = Planet(2, "Open", Position(80, 0), observed=True, habitability=70)
    scout = Fleet(
        2, "Scout", 1, Position(0, 0), role="scout", combat_power=5,
        native={"weapon_count": 1},
    )
    state = _state([_home(), fort, empty], [_colony_ship(), scout])
    orders = OrderSet("paper", 2425, 1)
    plan = BalancedPersona().build_plan(state)

    add_military_orders(state, orders, plan)
    add_economic_orders(state, orders, plan)

    attacks = [order for order in orders.orders if order.payload.get("paper_tiger")]
    assert len(attacks) == 1
    assert attacks[0].kind == "move_fleet"
    assert attacks[0].payload["mission"] == "attack"
    assert attacks[0].payload["destination_planet_id"] == 9
    assert state.native["paper_tiger_claims"] == [9]

    colonize = _colonize_orders(orders)
    assert colonize
    assert {order.payload["destination_planet_id"] for order in colonize} == {2}
    for order in colonize:
        destination = next(planet for planet in state.planets if planet.id == order.payload["destination_planet_id"])
        assert destination.owner is None
        assert not planet_has_starbase(destination)

    diagnostic = next(row for row in state.native["colony_target_diagnostics"] if row["planet_id"] == 9)
    assert diagnostic["hard_rejection_reason"] == "paper_tiger_left_to_military"
    assert diagnostic["blocked_by"] == "military_invasion"
    assert diagnostic["colonization_block"] == "foreign_owned_with_starbase"
    conversion = next(row for row in state.native["colony_capture_conversions"] if row["planet_id"] == 9)
    assert conversion["action"] == "PAPER_TIGER_LEFT_TO_MILITARY"
    assert conversion["status"] == "DROPPED_COLONIZATION"
    assert conversion["reason"] == "foreign_owned_with_starbase"


def test_occupied_fort_with_no_empty_world_drops_the_colony_order():
    state = _state([_home(), _fort()], [_colony_ship()], paper_tiger_claims=[9])
    orders = OrderSet("paper", 2425, 1)

    add_economic_orders(state, orders)

    assert _colonize_orders(orders) == []
    diagnostic = next(row for row in state.native["colony_target_diagnostics"] if row["planet_id"] == 9)
    assert diagnostic["hard_rejection_reason"] == "paper_tiger_left_to_military"
    assert "military path attacks" in next(
        row["policy"] for row in state.native["colony_capture_conversions"] if row["planet_id"] == 9
    )


def test_unowned_starbase_is_not_a_colony_target():
    base = Planet(
        4, "Empty Fort", Position(40, 0), observed=True, habitability=95,
        native={"has_starbase": True, "starbase_design": 0},
    )
    empty = Planet(2, "Open", Position(70, 0), observed=True, habitability=60)
    state = _state([_home(), base, empty], [_colony_ship()])
    orders = OrderSet("paper", 2425, 1)

    add_economic_orders(state, orders)

    colonize = _colonize_orders(orders)
    assert {order.payload["destination_planet_id"] for order in colonize} == {2}
    diagnostic = next(row for row in state.native["colony_target_diagnostics"] if row["planet_id"] == 4)
    assert diagnostic["hard_rejection_reason"] == "starbase_blocks_colonization"
    assert diagnostic["blocked_by"] == "starbase"
    assert colonization_block_reason(state, base) == "starbase_present"


def test_orbital_fort_without_an_owner_is_still_not_colonized():
    fort = _fort(owner=None)
    state = _state([_home(), fort], [_colony_ship()], paper_tiger_claims=[9])
    orders = OrderSet("paper", 2425, 1)

    add_economic_orders(state, orders)

    assert _colonize_orders(orders) == []
    diagnostic = next(row for row in state.native["colony_target_diagnostics"] if row["planet_id"] == 9)
    assert diagnostic["hard_rejection_reason"] == "paper_tiger_left_to_military"
    assert diagnostic["colonization_block"] == "starbase_present"


def test_scoring_skips_occupied_and_starbase_worlds_but_keeps_a_cleared_claim():
    fort = _fort()
    base = Planet(
        4, "Dock", Position(60, 0), observed=True, habitability=80,
        native={"has_starbase": True},
    )
    cleared = Planet(2, "Cleared", Position(30, 0), observed=True, habitability=55)
    state = _state(
        [_home(), fort, base, cleared], [_colony_ship()],
        paper_tiger_claims=[9, 2],
    )

    ranked = score_colony_candidates(state, state.fleets[0])

    assert [candidate.planet_id for candidate in ranked] == [2]


def test_cleared_claim_is_a_normal_colony_order():
    cleared = Planet(2, "Cleared", Position(40, 0), observed=True, habitability=80)
    state = _state([_home(), cleared], [_colony_ship()], paper_tiger_claims=[2])
    orders = OrderSet("paper", 2425, 1)

    add_economic_orders(state, orders)

    colonize = _colonize_orders(orders)
    assert len(colonize) == 1
    assert colonize[0].payload["destination_planet_id"] == 2
    assert colonize[0].payload["mission"] == "colonize"
    assert colonization_block_reason(state, cleared) is None


def test_writer_refuses_colonize_of_an_occupied_fort(tmp_path):
    root = resource_path("sandboxes", "sandbox-native-reference")
    m_path = root / "GAME.m1"
    xy_path = root / "GAME.xy"
    state = NativeCoreTurnAdapter(xy_path=xy_path).read_state(m_path, player_id=1)
    target = next(planet for planet in state.planets if planet.id != 0)
    target.owner = 6
    target.habitability = 90
    target.observed = True
    target.native = {
        **(target.native or {}),
        "has_starbase": True,
        "starbase_design": 1,
        "starbase_capabilities": {"is_orbital_fort": True},
        "owner_prt_name": "Alternate Reality",
    }
    orders = OrderSet(state.game_name, state.year, state.player_id)
    orders.add(
        "colony_operation",
        {
            "fleet_id": 1,
            "destination_planet_id": target.id,
            "warp": 6,
            "mission": "colonize",
        },
        "illegal colonize of an occupied fort",
    )

    result = write_native_orders(
        game_state=state,
        orders=orders,
        template_x_path=root / "backup" / "GAME.x1",
        output_x_path=tmp_path / "GAME.x1",
    )

    skipped = [item for item in result.skipped if item["kind"] == "colony_operation"]
    assert skipped
    assert "owned by another player" in skipped[0]["reason"]
    assert skipped[0]["payload"]["colonization_block"] == "foreign_owned_with_starbase"
    assert all(item["kind"] != "colony_operation" for item in result.emitted)
