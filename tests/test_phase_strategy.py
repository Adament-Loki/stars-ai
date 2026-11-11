
from dataclasses import dataclass
from stars_ai.phase_strategy import (
    StrategicPhase,
    StrategicPhaseManager,
    PlanetPreference,
    planet_meets_preference,
)
from stars_ai.models import Fleet, GameState, Planet, Position, RaceProfile, Tech
from stars_ai.persona import BalancedPersona


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
    opening_min_planets = 8
    midgame_planets = 12
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


def test_first_contact_does_not_end_early_expansion():
    state = S(
        [P(owner_id=1, observed=True)]
        + [P(owner_id=None, observed=False) for _ in range(5)]
        + [P(owner_id=2, observed=True)],
        fleets=[F(owner_id=2)],
    )

    decision = StrategicPhaseManager().decide(state, Persona())

    assert decision.phase == StrategicPhase.EARLY_EXPANSION
    assert decision.policy.expand_weight > 2.0
    assert decision.policy.fortify_weight < 1.0


def test_border_contact_during_the_explore_clock_keeps_expanding():
    owned = [
        Planet(index, f"P{index}", Position(index * 80, 300), owner=1, observed=True, habitability=80)
        for index in range(8)
    ]
    owned.append(Planet(20, "Border", Position(0, 0), owner=1, observed=True, habitability=70))
    state = GameState(
        "contact", 2420, 1, RaceProfile(), Tech(),
        owned + [
            Planet(30 + index, f"Open {index}", Position(800, index * 30), observed=False)
            for index in range(12)
        ] + [
            Planet(90, "Enemy", Position(40, 0), owner=2, observed=True, habitability=60),
        ],
        [Fleet(1, "Raider", 2, Position(30, 0), role="combat")],
    )

    decision = StrategicPhaseManager().decide(state, Persona())

    assert decision.phase == StrategicPhase.EARLY_EXPANSION
    assert decision.assessment.hostile_worlds >= 1
    assert decision.policy.expand_weight > decision.policy.fortify_weight


def test_internal_clock_changes_the_job_without_counting_planets():
    state = _midgame_state()

    state.year = 2410
    opening = StrategicPhaseManager().decide(state, Persona())
    assert opening.phase == StrategicPhase.EARLY_EXPANSION

    state.year = 2435
    develop = StrategicPhaseManager().decide(state, Persona())
    assert develop.phase == StrategicPhase.INDUSTRIAL_BUILDOUT
    assert develop.policy.develop_weight > develop.policy.expand_weight

    state.year = 2475
    fortify = StrategicPhaseManager().decide(state, Persona())
    assert fortify.phase == StrategicPhase.FORTIFICATION
    assert fortify.policy.fortify_weight > 1.0
    assert fortify.policy.research_weight > 1.0
    assert fortify.policy.develop_weight > 1.0
    assert fortify.policy.attack_weight < 1.0

    state.year = 2520
    growth = StrategicPhaseManager().decide(state, Persona())
    assert growth.phase == StrategicPhase.OPPORTUNISTIC_WAR
    assert growth.policy.attack_weight > 1.5
    assert growth.policy.expand_weight > 1.0


def test_pressed_home_bunkers_and_an_adjacent_ally_does_not():
    home = Planet(0, "Home", Position(0, 0), owner=1, observed=True, habitability=100)
    raider = Fleet(1, "Raider", 2, Position(30, 0), role="combat")
    pressed = GameState("pressed", 2406, 1, RaceProfile(), Tech(), [home], [raider])

    bunker = StrategicPhaseManager().decide(pressed, Persona())

    assert bunker.phase == StrategicPhase.FORTIFICATION
    assert bunker.policy.fortify_weight > bunker.policy.expand_weight

    allied = GameState(
        "ally", 2406, 1, RaceProfile(), Tech(), [home],
        [Fleet(2, "Friend", 2, Position(30, 0), role="combat")],
        native={"configured_allies": [2]},
    )
    opening = StrategicPhaseManager().decide(allied, Persona())
    assert opening.phase == StrategicPhase.EARLY_EXPANSION


def _midgame_state():
    worlds = [
        Planet(
            index, f"Core{index}", Position(index * 40, 500), owner=1, observed=True,
            habitability=80, population=40_000, factories=2,
        )
        for index in range(10)
    ]
    worlds.append(Planet(
        10, "Yard", Position(0, 500), owner=1, observed=True, habitability=90,
        population=200_000, factories=200,
        native={"starbase_capabilities": {"can_build_ships": True}},
    ))
    worlds.append(Planet(
        11, "Campus", Position(80, 500), owner=1, observed=True, habitability=90,
        population=200_000, factories=200,
    ))
    worlds.append(Planet(
        12, "Border", Position(0, 0), owner=1, observed=True, habitability=70,
        population=80_000, factories=8,
    ))
    worlds.append(Planet(13, "Enemy", Position(40, 0), owner=2, observed=True, habitability=60))
    return GameState(
        "midgame", 2430, 1, RaceProfile(), Tech(), worlds,
        [Fleet(9, "Foreign", 2, Position(40, 0), role="combat", combat_power=20)],
    )


def test_persona_assigns_midgame_jobs_per_planet():
    plan = BalancedPersona().build_plan(_midgame_state())

    assert plan.phase == StrategicPhase.INDUSTRIAL_BUILDOUT.value
    assert plan.planet_actions[12] == "hold_line"
    assert plan.planet_actions[10] == "ships"
    assert plan.planet_actions[11] == "tech"
    assert plan.planet_actions[0] == "industrialize"
    assert plan.objective("develop") > plan.objective("expand")
    assert "hostile_pressure" in plan.phase_assessment


def test_develop_clock_strengthens_instead_of_keeping_the_opening_sprint():
    decision = StrategicPhaseManager().decide(_midgame_state(), Persona())

    assert decision.phase == StrategicPhase.INDUSTRIAL_BUILDOUT
    assert decision.policy.fortify_weight > 1.0
    assert decision.policy.develop_weight > decision.policy.expand_weight


def test_selective_planet_policy_rejects_marginal_world():
    rich_only = PlanetPreference(min_habitability=50, min_resource_score=0.60, selectivity=0.75)
    marginal = P(observed=True, habitability=35, ironium_conc=30, boranium_conc=30, germanium_conc=30)
    assert not planet_meets_preference(marginal, rich_only)


def test_low_selectivity_accepts_good_enough_world():
    broad = PlanetPreference(selectivity=0.10)
    world = P(observed=True, habitability=45, ironium_conc=40, boranium_conc=40, germanium_conc=40)
    assert planet_meets_preference(world, broad)
