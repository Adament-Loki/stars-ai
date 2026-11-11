from stars_ai.capability_execution import design_is_blocked
from stars_ai.critical_assets import (
    capital_missile_demand,
    fortress_construction_outlook,
    fortress_funding_years,
    fortress_research_demand,
    release_blocked_fortress_design,
    select_critical_planets,
)
from stars_ai.models import GameState, Planet, Position, RaceProfile, Tech


def test_homeworld_leads_the_critical_list_and_the_list_stops_at_ten():
    planets = [
        Planet(0, "Home", Position(0, 0), owner=1, population=100_000, habitability=80,
               native={"is_homeworld": True}),
    ]
    for index in range(1, 15):
        planets.append(Planet(
            index, f"World {index}", Position(index * 20, 0), owner=1,
            population=50_000 + index * 10_000, habitability=70,
        ))
    state = GameState("critical", 2445, 1, RaceProfile(), Tech(), planets, [])

    chosen = select_critical_planets(state)

    assert len(chosen) == 10
    assert chosen[0].name == "Home"
    assert all(planet.owner == 1 for planet in chosen)


def test_fortress_research_waits_until_turn_35_and_then_requires_delta():
    too_early = GameState("critical", 2434, 1, RaceProfile(), Tech(weapons=4), [], [])
    assert fortress_research_demand(too_early) is None
    assert too_early.native["fortress_research_hold"] is False

    opening = GameState("critical", 2435, 1, RaceProfile(), Tech(weapons=6), [], [])
    demand = fortress_research_demand(opening)
    assert demand is not None
    assert demand.military_emergency is True
    assert demand.capability.name == "Delta Torpedo"
    assert opening.native["fortress_research_hold"] is True

    ready = GameState("critical", 2435, 1, RaceProfile(), Tech(weapons=10, propulsion=2), [], [])
    assert fortress_research_demand(ready) is None
    assert ready.native["fortress_research_hold"] is False


def test_a_missing_seed_block_is_released_once_fortress_planning_starts():
    state = GameState("critical", 2435, 1, RaceProfile(), Tech(), [], [], native={
        "capability_execution": {
            "design:starbase:defense": {
                "status": "BLOCKED_NATIVE",
                "blocking_reason": "no safe client baseline exists",
            }
        }
    })

    release_blocked_fortress_design(state)

    assert design_is_blocked(state, "starbase", is_starbase=True, purpose="defense") is False

    later = GameState("critical", 2465, 1, RaceProfile(), Tech(), [], [], native={
        "capability_execution": {
            "design:starbase:defense": {
                "status": "BLOCKED_NATIVE",
                "blocking_reason": "no safe client baseline exists",
            }
        }
    })
    release_blocked_fortress_design(later)
    assert design_is_blocked(later, "starbase", is_starbase=True, purpose="defense") is False


def test_fortress_funding_years_uses_the_current_mine_rate():
    planet = Planet(
        1, "Hoth", Position(0, 0), owner=1, population=100_000, mines=20,
        native={"mineral_concentrations": [100, 100, 100]},
    )
    state = GameState("critical", 2445, 1, RaceProfile(), Tech(), [planet], [])

    years = fortress_funding_years(state, planet, {"ironium": 0, "boranium": 0, "germanium": 80})

    assert years == 4


def test_jihad_is_the_turn_50_upgrade_and_the_next_missile_follows_it():
    opening = GameState("critical", 2450, 1, RaceProfile(), Tech(weapons=8, propulsion=6), [], [])
    demand = capital_missile_demand(opening)
    assert demand is not None
    assert demand.capability.name == "Jihad Missile"
    assert opening.native.get("fortress_research_hold") is not True

    armed = GameState(
        "critical", 2456, 1, RaceProfile(), Tech(weapons=12, propulsion=6), [], [],
    )
    nxt = capital_missile_demand(armed)
    assert nxt is not None
    assert nxt.capability.name == "Juggernaut Missile"


def test_imports_and_resources_set_the_construction_window():
    colony = Planet(
        1, "Hoth", Position(0, 0), owner=1, population=100_000, mines=20, factories=100,
        native={"critical_asset": True, "mineral_concentrations": [100, 100, 100]},
    )
    home = Planet(
        0, "Home", Position(40, 0), owner=1, population=200_000, mines=40,
        germanium=500,
        native={"is_homeworld": True, "mineral_concentrations": [100, 100, 100]},
    )
    state = GameState("critical", 2440, 1, RaceProfile(), Tech(energy=5), [home, colony], [])

    outlook = fortress_construction_outlook(state, colony, {"germanium": 80}, resource_remaining=400)

    assert "Home" in outlook["import_from"]
    assert outlook["mineral_years"] is not None and outlook["mineral_years"] <= 4
    assert outlook["resource_years"] is not None
    assert outlook["window_years"] == max(outlook["mineral_years"], outlook["resource_years"])
