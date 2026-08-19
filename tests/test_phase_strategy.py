
from dataclasses import dataclass
from stars_ai.phase_strategy import (
    StrategicPhase,
    StrategicPhaseManager,
    PlanetPreference,
    planet_meets_preference,
)


@dataclass
class P:
    owner_id: int | None = None
    observed: bool = False
    habitability: float | None = None
    ironium_conc: int | None = None
    boranium_conc: int | None = None
    germanium_conc: int | None = None


@dataclass
class F:
    owner_id: int


class S:
    player_id = 1
    def __init__(self, planets, fleets=()):
        self.planets = planets
        self.fleets = list(fleets)


class Persona:
    opening_min_planets = 4
    frontier_contact_threshold = 0.18
    expansion_saturation_threshold = 0.55
    transition_style = "consolidate"


def test_all_personas_share_aggressive_opening():
    state = S([
        P(owner_id=1, observed=True, habitability=80),
        P(owner_id=None, observed=False),
        P(owner_id=None, observed=False),
        P(owner_id=None, observed=False),
        P(owner_id=None, observed=False),
        P(owner_id=None, observed=False),
    ])
    d = StrategicPhaseManager().decide(state, Persona())
    assert d.phase == StrategicPhase.EARLY_EXPANSION
    assert d.policy.explore_weight > 1.0
    assert d.policy.expand_weight > 1.0
    assert d.policy.attack_weight < 1.0


def test_militarist_transitions_to_opportunistic_war():
    p = Persona()
    p.transition_style = "attack"
    state = S(
        [P(owner_id=1, observed=True) for _ in range(6)]
        + [P(owner_id=2, observed=True) for _ in range(4)],
        fleets=[F(owner_id=2)]
    )
    d = StrategicPhaseManager().decide(state, p)
    assert d.phase == StrategicPhase.OPPORTUNISTIC_WAR
    assert d.policy.attack_weight > d.policy.expand_weight


def test_fortifier_transitions_to_fortification():
    p = Persona()
    p.transition_style = "fortify"
    state = S(
        [P(owner_id=1, observed=True) for _ in range(6)]
        + [P(owner_id=2, observed=True) for _ in range(4)],
        fleets=[F(owner_id=2)]
    )
    d = StrategicPhaseManager().decide(state, p)
    assert d.phase == StrategicPhase.FORTIFICATION
    assert d.policy.fortify_weight > d.policy.attack_weight


def test_selective_planet_policy_rejects_marginal_world():
    rich_only = PlanetPreference(min_habitability=50, min_resource_score=0.60, selectivity=0.75)
    marginal = P(observed=True, habitability=35, ironium_conc=30, boranium_conc=30, germanium_conc=30)
    assert not planet_meets_preference(marginal, rich_only)


def test_low_selectivity_accepts_good_enough_world():
    broad = PlanetPreference(selectivity=0.10)
    world = P(observed=True, habitability=45, ironium_conc=40, boranium_conc=40, germanium_conc=40)
    assert planet_meets_preference(world, broad)
