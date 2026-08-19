from stars_ai.models import GameState, RaceProfile, Tech, Planet, Fleet, Position
from stars_ai.agent import StarsAgent
from stars_ai.persona import ExpansionistPersona, IndustrialistPersona, MilitaristPersona, TechnologistPersona, persona_from_name


def fixture_state():
    return GameState(
        game_name="persona-test", year=2400, player_id=0,
        race=RaceProfile(name="Test"), tech=Tech(3,3,3,3,3,3),
        planets=[
            Planet(0, "Home", Position(0,0), owner=0, population=100000, factories=10, mines=10),
            Planet(1, "Green", Position(50,0), owner=None, habitability=60, observed=True),
            Planet(2, "Unknown", Position(120,0), owner=None, observed=False),
        ],
        fleets=[
            Fleet(0, "Scout", 0, Position(0,0), role="scout", speed=7),
            Fleet(1, "Colony", 0, Position(0,0), role="colony", speed=7),
            Fleet(2, "War", 0, Position(0,0), role="combat", combat_power=100, speed=7),
        ],
    )


def test_persona_factory():
    assert persona_from_name("expansionist").name == "Expansionist"
    assert persona_from_name("MILITARIST").name == "Militarist"


def test_expansionist_changes_macro_plan():
    state = fixture_state()
    agent = StarsAgent(state, persona=ExpansionistPersona())
    orders = agent.play_turn()
    assert agent.last_plan is not None
    assert agent.last_plan.objective("expand") > agent.last_plan.objective("defend")
    assert agent.last_plan.research("propulsion") > agent.last_plan.research("weapons")
    assert any(o.kind in ("move_fleet","colony_operation") and o.payload.get("mission") == "colonize" for o in orders.orders)


def test_personas_choose_different_research_priorities():
    state = fixture_state()
    exp_orders = StarsAgent(state, persona=ExpansionistPersona()).play_turn()
    mil_orders = StarsAgent(state, persona=MilitaristPersona()).play_turn()
    tech_orders = StarsAgent(state, persona=TechnologistPersona()).play_turn()
    exp = next(o for o in exp_orders.orders if o.kind == "set_research")
    mil = next(o for o in mil_orders.orders if o.kind == "set_research")
    tech = next(o for o in tech_orders.orders if o.kind == "set_research")
    assert exp.payload["field"] == "propulsion"
    assert mil.payload["field"] == "weapons"
    assert tech.payload["field"] == "electronics"


def test_industrialist_prioritizes_development():
    plan = IndustrialistPersona().build_plan(fixture_state())
    assert plan.objective("develop") > plan.objective("attack")
    assert plan.planet("factories") > plan.planet("ships")


def test_explicit_reach_tech_goal_overrides_persona_when_gap_is_large():
    from stars_ai.goals import ReachTechGoal
    persona = ExpansionistPersona().with_goals(ReachTechGoal("electronics", 10, priority=2.0))
    agent = StarsAgent(fixture_state(), persona=persona)
    orders = agent.play_turn()
    research = next(o for o in orders.orders if o.kind == "set_research")
    assert research.payload["field"] == "electronics"
    assert agent.memory.goal_progress["reach-tech:electronics:10"] == 0.3


def test_own_planets_goal_increases_expansion_pressure():
    from stars_ai.goals import OwnPlanetsGoal
    base = IndustrialistPersona().build_plan(fixture_state())
    goal_plan = IndustrialistPersona().with_goals(OwnPlanetsGoal(8, priority=2.0)).build_plan(fixture_state())
    assert goal_plan.objective("expand") > base.objective("expand")
    assert goal_plan.mission("colonize") > base.mission("colonize")
