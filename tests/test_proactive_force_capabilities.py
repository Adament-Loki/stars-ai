from types import SimpleNamespace

from stars_ai.models import GameState, RaceProfile, Tech, Planet, Fleet, Position
from stars_ai.territorial_defense import assess_territorial_defense, hostile_colonies_inside_claimed_area


def _plan():
    return SimpleNamespace(
        defense_radius=100.0,
        diplomacy={
            2: {
                "attitude": "hostile",
                "native_relation": 0,
                "conflict": {"recommended_action": "oppose"},
            }
        },
        attack_strength_ratio=1.25,
        risk_tolerance=0.5,
    )


def _state():
    home=Planet(
        0,"Home",Position(0,0),owner=1,observed=True,habitability=100,
        population=400000,native={"is_homeworld":True},
    )
    enemy=Planet(
        1,"Enemy Edge",Position(90,0),owner=2,observed=True,habitability=70,
        population=50000,native={},
    )
    return GameState(
        "g",2425,1,RaceProfile(),Tech(),[home,enemy],[],
        native={"objects":[],"design_profiles":[],"production_by_planet":{}},
    )


def test_hostile_colony_inside_claimed_area_is_capability_signal():
    state=_state()
    targets=hostile_colonies_inside_claimed_area(state,_plan())
    assert [planet.id for planet in targets] == [1]


def test_neutral_colony_is_not_automatic_bomber_target():
    state=_state()
    plan=_plan()
    plan.diplomacy[2]["attitude"]="neutral"
    plan.diplomacy[2]["conflict"]["recommended_action"]="coexist"
    assert hostile_colonies_inside_claimed_area(state,plan) == ()


def test_significant_transport_just_outside_claim_radius_creates_edge_mine_demand():
    state=_state()
    state.planets[1].position=Position(400,0)
    state.fleets.append(Fleet(
        7,"Incoming Transport",2,Position(160,0),role="freighter",
        cargo_capacity=800,
        native={"cargo":{"population":800}},
    ))
    assessment=assess_territorial_defense(state,_plan())
    assert not assessment.needs_response
    assert assessment.needs_minefield
    assert assessment.desired_minelayers >= 1
    assert 0 in assessment.uncovered_minefield_anchor_ids


def test_transport_inside_claim_zone_gets_minefield_response():
    state=_state()
    state.planets[1].position=Position(400,0)
    state.fleets.append(Fleet(
        8,"Deep Transport",2,Position(120,0),role="freighter",
        cargo_capacity=800,
        native={"cargo":{"population":800}},
    ))
    assessment=assess_territorial_defense(state,_plan())
    assert assessment.violations
    assert assessment.violations[0].classification in {"transport","armed_transport"}
    assert assessment.violations[0].requires_minefield is True
