from stars_ai.models import Fleet, GameState, Planet, Position, RaceProfile, Tech
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


def _battle_hull_state(*, construction: int, hostile: bool = False):
    worlds = [
        Planet(
            index, f"Poor{index}", Position(index * 20, 0), owner=1, observed=True,
            habitability=41, population=80_000,
            native={"environment": (50, 15, 50), "original_environment": (50, 15, 50)},
        )
        for index in range(4)
    ]
    worlds[0].native["starbase_capabilities"] = {"can_build_ships": True}
    fleets = []
    native = {"design_profiles": []}
    if hostile:
        fleets.append(Fleet(9, "Enemy", 2, Position(30, 0), role="combat", combat_power=400))
        native["designs"] = [{
            "design_number": 2, "name": "Escort", "hull_id": 5, "is_starbase": False,
            "slots": [
                {"category": 1, "item_id": 2, "count": 1},
                {"category": 16, "item_id": 3, "count": 1},
            ],
        }]
        native["design_profiles"] = [{
            "design_number": 2, "name": "Escort", "role": "combat", "hull_id": 5,
        }]
    return GameState(
        "hulls", 2450, 1,
        RaceProfile(native={
            "population_efficiency_raw": 10,
            "hab_center": (50, 50, 50),
            "hab_low": (15, 15, 15),
            "hab_high": (85, 85, 85),
            "hab_immune": (False, False, False),
        }),
        Tech(construction=construction, energy=8, weapons=8, propulsion=12, biotechnology=4),
        worlds, fleets, native=native,
    )


def test_cruiser_outranks_gravity_terraforming_after_the_freighter():
    state = _battle_hull_state(construction=8)
    decision = plan_research(state, BalancedPersona().build_plan(state))
    assert decision.capability_name == "Cruiser"
    assert decision.current_field == "construction"
    assert decision.requirements == {"construction": 9}
    assert state.native["research_goals"][0]["tier"] == "fleet"


def test_battle_hull_ladder_is_galleon_then_battleship_then_nubian():
    galleon = plan_research(_battle_hull_state(construction=9), BalancedPersona().build_plan(_battle_hull_state(construction=9)))
    assert galleon.capability_name == "Galleon"
    assert galleon.requirements == {"construction": 11}
    battleship = plan_research(_battle_hull_state(construction=11), BalancedPersona().build_plan(_battle_hull_state(construction=11)))
    assert battleship.capability_name == "Battleship"
    assert battleship.requirements == {"construction": 13}
    far = _battle_hull_state(construction=13)
    plan_research(far, BalancedPersona().build_plan(far))
    nubian_points = far.native["preference_trace"].get("hull:29") or []
    assert any(row["name"] == "hull_nubian" for row in nubian_points)


def test_a_fielded_weapon_upgrade_still_beats_the_next_battle_hull():
    state = _battle_hull_state(construction=8, hostile=True)
    state.fleets.append(Fleet(
        3, "Fleet", 1, Position(0, 0), role="combat", combat_power=300,
        native={"ship_count": [0, 0, 4]},
    ))
    decision = plan_research(state, BalancedPersona().build_plan(state))
    assert decision.current_field == "weapons"
    assert decision.capability_id.startswith("military:weapons_upgrade:")


def test_preference_points_rank_alternate_reality_energy_above_the_death_star():
    home = Planet(
        0, "Home", Position(0, 0), owner=1, population=400_000,
        native={"starbase_capabilities": {"can_build_ships": True}},
    )
    early = GameState(
        "ar", 2440, 1,
        RaceProfile(primary_trait="Alternate Reality", native={"prt_id": 8, "population_efficiency_raw": 10}),
        Tech(energy=0, construction=8),
        [home], [],
        native={"design_profiles": []},
    )
    early_decision = plan_research(early, BalancedPersona().build_plan(early))
    assert early_decision.current_field == "energy"
    assert any(row["name"] == "ar_energy" for row in early.native["preference_trace"][early_decision.capability_id])

    later = GameState(
        "ar", 2460, 1,
        RaceProfile(primary_trait="Alternate Reality", native={"prt_id": 8, "population_efficiency_raw": 10}),
        Tech(energy=9, construction=8, weapons=6, propulsion=4, electronics=4, biotechnology=3),
        [home], [],
        native={"design_profiles": []},
    )
    later_decision = plan_research(later, BalancedPersona().build_plan(later))
    assert later_decision.capability_name == "Death Star"
    assert later_decision.requirements["construction"] == 17


def test_weapons_preference_matches_observed_enemy_fleets_and_stops_once_ahead():
    behind = _battle_hull_state(construction=8)
    behind.native["enemy_design_profiles"] = [{"weapons": 12}]
    behind_decision = plan_research(behind, BalancedPersona().build_plan(behind))
    assert behind_decision.current_field == "weapons"
    assert any(row["name"] == "weapon_parity" for row in behind.native["research_goals"][0]["preferences"])

    ahead = _battle_hull_state(construction=8)
    ahead.tech.weapons = 14
    ahead.native["enemy_design_profiles"] = [{"weapons": 12}]
    ahead_decision = plan_research(ahead, BalancedPersona().build_plan(ahead))
    assert ahead_decision.current_field != "weapons"


def test_bleeding_edge_adds_energy_points_that_a_plain_race_does_not_get():
    from stars_ai.research_capabilities import ResearchCapability
    from stars_ai.research_preferences import preference_points

    capability = ResearchCapability(
        capability_id="probe:energy",
        name="Energy probe",
        category="economy",
        requirements={"energy": 6},
        post_unlock_action="none",
        source="test",
    )
    plain = GameState("plain", 2450, 1, RaceProfile(), Tech(energy=5), [], [])
    bleeding = GameState(
        "bet", 2450, 1,
        RaceProfile(native={"lrts": ["BET"]}),
        Tech(energy=5), [], [],
    )
    plain_total, plain_rows = preference_points(plain, capability)
    bet_total, bet_rows = preference_points(bleeding, capability)
    assert plain_total < 0
    assert any(name == "energy_plain" for name, _points in plain_rows)
    assert bet_total > plain_total
    assert any(name == "bleeding_edge_energy" for name, _points in bet_rows)


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
