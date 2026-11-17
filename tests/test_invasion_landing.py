"""Stars! landing math replaces the old pop * (1.1 + defs/100) estimate."""

from stars_ai.invasion import plan_invasion, troops_required_to_land
from stars_ai.models import GameState, Planet, Position, RaceProfile, Tech


def test_faq_landing_beats_defenders_after_coverage():
    # No defenses: 1.1 * troops > 1000 defenders => 910 troops (floor + 1).
    assert troops_required_to_land(1000, 0) == 910
    # War Monger attack 1.65 needs fewer.
    assert troops_required_to_land(1000, 0, attacker_is_war_monger=True) < 910
    # Inner Strength defense 2.0 needs more than a normal garrison.
    normal = troops_required_to_land(1000, 0)
    inner = troops_required_to_land(1000, 0, defender_is_inner_strength=True)
    assert inner > normal
    # 75% of full coverage leaves a quarter of the troops.
    covered = troops_required_to_land(1000, 0, coverage=1.0)
    assert covered == 3637


def test_war_monger_still_loses_the_exchange_to_inner_strength():
    # FAQ 4.10: IS defense 2.0 is stronger than WM attack 1.65.
    wm_vs_is = troops_required_to_land(
        1000, 0, attacker_is_war_monger=True, defender_is_inner_strength=True,
    )
    wm_vs_normal = troops_required_to_land(1000, 0, attacker_is_war_monger=True)
    assert wm_vs_is > wm_vs_normal


def test_starbase_blocks_the_landing():
    planet = Planet(
        3, "Fort", Position(10, 0), owner=2, population=20_000, habitability=80,
        native={"has_starbase": True, "strategic_value": 0.8},
    )
    state = GameState(
        "land", 2450, 1,
        RaceProfile(name="WM", primary_trait="War Monger", native={"prt_id": 2}),
        Tech(), [planet], [],
    )
    plan = plan_invasion(
        planet, our_local_strength=500, enemy_local_strength=1,
        transport_capacity=100_000, state=state,
    )
    assert plan.action == "BYPASS"
    assert "starbase" in plan.reason.casefold()


def test_open_world_reports_the_landing_requirement():
    planet = Planet(
        4, "Colony", Position(20, 0), owner=2, population=10_000, habitability=70,
        factories=100, native={"strategic_value": 0.8},
    )
    state = GameState(
        "land", 2450, 1,
        RaceProfile(name="JOAT", primary_trait="JOAT", native={"prt_id": 9}),
        Tech(), [planet], [],
    )
    plan = plan_invasion(
        planet, our_local_strength=500, enemy_local_strength=1,
        transport_capacity=500_000, state=state,
    )
    assert plan.action == "CAPTURE"
    assert plan.required_population == troops_required_to_land(10_000, 0)
    assert "0.75" in plan.reason
