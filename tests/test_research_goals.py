from stars_ai.models import GameState, Planet, Position, RaceProfile, Tech
from stars_ai.planet_economy import estimated_operating_resources, decode_race_economy
from stars_ai.research_cost import level_research_cost, resources_to_requirements
from stars_ai.research_planner import plan_research
from stars_ai.persona import BalancedPersona


def test_standard_research_cost_matches_the_stars_level_table():
    race = RaceProfile()
    assert level_research_cost(race, "energy", 1) == 50
    assert level_research_cost(race, "weapons", 2) == 80
    assert level_research_cost(race, "construction", 3) == 130
    # Reaching level 3 costs every earlier step, not the price of level 3 alone.
    assert resources_to_requirements(race, Tech(), {"energy": 3}) == 50 + 80 + 130
    assert resources_to_requirements(race, Tech(construction=3), {"construction": 4}) == 210


def test_racial_research_settings_are_half_or_seventy_five_percent_extra():
    # File codes: 0 = 75% extra, 1 = standard, 2 = 50% less.
    race = RaceProfile(native={"research_cost_raw": [2, 0, 1, 1, 1, 1]})
    assert level_research_cost(race, "energy", 1) == 25
    assert level_research_cost(race, "weapons", 1) == 87
    assert level_research_cost(race, "propulsion", 1) == 50
    assert resources_to_requirements(race, Tech(), {"energy": 3}) == (50 + 80 + 130) // 2


def test_alternate_reality_energy_multiplies_population_resources():
    economy = decode_race_economy(RaceProfile(
        primary_trait="Alternate Reality",
        native={"prt_id": 8, "population_efficiency_raw": 10},
    ))
    planet = Planet(0, "Home", Position(0, 0), owner=1, population=100_000)
    base = estimated_operating_resources(planet, economy, energy_tech=0)
    doubled = estimated_operating_resources(planet, economy, energy_tech=1)
    assert base["factory_resources"] == 0
    assert base["estimated_resources"] == 10
    assert doubled["estimated_resources"] == 20


def test_alternate_reality_researches_energy_ahead_of_a_transport_hull():
    home = Planet(
        0, "Home", Position(0, 0), owner=1, population=400_000,
        native={"starbase_capabilities": {"can_build_ships": True}},
    )
    state = GameState(
        "ar", 2420, 1,
        RaceProfile(primary_trait="Alternate Reality", native={"prt_id": 8, "population_efficiency_raw": 10}),
        Tech(energy=0, construction=3),
        [home], [],
        native={"design_profiles": [{
            "design_number": 1, "name": "Freighter", "role": "freighter", "cargo_capacity": 120,
        }]},
    )
    decision = plan_research(state, BalancedPersona().build_plan(state))
    assert decision.current_field == "energy"
    assert decision.capability_id.startswith("race:ar_energy:")
    assert state.native["research_goals"][0]["tier"] == "race_resource"


def test_many_weak_worlds_make_terraforming_a_growth_goal():
    worlds = [
        Planet(
            index, f"Poor{index}", Position(index * 20, 0), owner=1, observed=True,
            habitability=41, population=80_000,
            native={"environment": (50, 15, 50), "original_environment": (50, 15, 50)},
        )
        for index in range(4)
    ]
    worlds[0].native["starbase_capabilities"] = {"can_build_ships": True}
    state = GameState(
        "bio", 2430, 1,
        RaceProfile(native={
            "population_efficiency_raw": 10,
            "hab_center": (50, 50, 50),
            "hab_low": (15, 15, 15),
            "hab_high": (85, 85, 85),
            "hab_immune": (False, False, False),
        }),
        Tech(energy=1, biotechnology=0),
        worlds, [],
        native={"design_profiles": []},
    )
    decision = plan_research(state, BalancedPersona().build_plan(state))
    growth = [row for row in state.native["research_goals"] if row["tier"] == "growth"]
    assert growth
    assert "colonists/year" in decision.reason or any("Terraform" in row["name"] for row in growth)
