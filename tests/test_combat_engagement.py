"""Combat doctrine and the battle simulator gate military orders."""

from stars_ai.battle_simulator import BattleDesign, BattleSide, BattleStack, Weapon, WeaponKind, simulate_battle
from stars_ai.combat_doctrine import ModernizationDecision, RelativeMilitaryAssessment, choose_modernization_plan
from stars_ai.combat_engagement import assess_turn_combat, engagement_allowed, publish_turn_combat
from stars_ai.models import Fleet, GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.persona import BalancedPersona
from stars_ai.strategy.military import add_military_orders


def _state(profiles, fleets):
    return GameState(
        "combat",
        2450,
        1,
        RaceProfile(name="R", primary_trait="JOAT"),
        Tech(weapons=10),
        [Planet(1, "Home", Position(0, 0), owner=1, native={"is_homeworld": True})],
        fleets,
        native={"design_profiles": profiles},
    )


def _laser_profile(slot=0):
    return {
        "design_number": slot,
        "name": "Beam",
        "hull_id": 6,
        "armor": 200,
        "resource_cost": 40,
        "slots": [{"category": 16, "item_id": 0, "count": 1}],
        "role": "combat",
    }


def test_losing_simulation_blocks_engagement():
    profiles = [_laser_profile(0)]
    ours = Fleet(
        1, "Patrol", 1, Position(0, 0), role="combat", combat_power=30,
        native={"ship_count": [1]},
    )
    enemy = Fleet(
        2, "Host", 2, Position(10, 0), role="combat", combat_power=400,
        native={"ship_count": [8]},
    )
    state = _state(profiles, [ours, enemy])
    plan = BalancedPersona().build_plan(state)
    report = publish_turn_combat(state, plan)
    assert report["authoritative"] is True
    state.native["turn_combat"] = {
        "decision": ModernizationDecision.FIGHT_NOW.value,
        "reason": report["reason"],
        "authoritative": True,
        "offensive": True,
    }
    assert engagement_allowed(state, [ours], [enemy]) is False
    orders = OrderSet("combat", 2450, 1)
    add_military_orders(state, orders, plan)
    assert any(note.startswith("COMBAT DOCTRINE:") for note in orders.notes)


def test_retreat_stops_offensive_orders():
    assessment = RelativeMilitaryAssessment(
        our_value=10, enemy_value=100, relative_strength=0.1,
        our_modern_fraction=1, enemy_modern_fraction=1,
        our_avg_tech=5, enemy_avg_tech=12, tech_gap=7,
        expected_trade_ratio=0.2,
    )
    plan = choose_modernization_plan(
        assessment,
        current_design_efficiency=1,
        prospective_design_efficiency=2,
        turns_to_upgrade=4,
        territorial_risk=0.9,
        sacrifice_budget=0.2,
        core_at_risk=True,
    )
    assert plan.decision == ModernizationDecision.RETREAT_AND_PRESERVE
    state = _state([_laser_profile()], [
        Fleet(1, "Patrol", 1, Position(0, 0), role="combat", combat_power=20, native={"ship_count": [1]}),
        Fleet(2, "Host", 2, Position(5, 0), role="combat", combat_power=200, native={"ship_count": [6]}),
    ])
    state.native["turn_combat"] = {
        "decision": plan.decision.value,
        "reason": plan.reason,
        "authoritative": True,
        "offensive": False,
    }
    assert engagement_allowed(state, [state.fleets[0]], [state.fleets[1]]) is False


def test_favorable_battle_is_allowed_when_the_doctrine_fights():
    attacker = BattleSide("us", [BattleStack(BattleDesign(
        "beam", armor=200, shields=0, movement=1, resource_cost=20,
        weapons=[Weapon(WeaponKind.BEAM, 40, 2, 10, 1, 2)],
    ), 4)])
    defender = BattleSide("them", [BattleStack(BattleDesign(
        "scout", armor=20, shields=0, movement=1, resource_cost=5,
        weapons=[Weapon(WeaponKind.BEAM, 4, 1, 1, 1, 1)],
    ), 1)])
    outcome = simulate_battle(attacker, defender)
    assert outcome.winner == "us"
    assert outcome.attacker_survivors > 0
    state = _state([], [])
    state.native["turn_combat"] = {
        "decision": "fight_now",
        "authoritative": False,
        "offensive": True,
    }
    assert engagement_allowed(state, [], []) is True


def test_assess_without_weapons_is_not_authoritative():
    state = _state([], [Fleet(1, "Scout", 1, Position(0, 0), role="scout")])
    report = assess_turn_combat(state, None)
    assert report["authoritative"] is False
    assert report["offensive"] is True
