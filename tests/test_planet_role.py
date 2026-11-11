from stars_ai.empire_optimizer import classify_planet_role, mineral_value
from stars_ai.models import Fleet, GameState, Planet, Position, RaceProfile, Tech
from stars_ai.phase_strategy import assign_planet_roles


def _planet(**kwargs):
    native = kwargs.pop("native", {})
    return Planet(1, "World", Position(0, 0), owner=1, observed=True, native=native, **kwargs)


def _state(planet, extras=()):
    return GameState("role", 2450, 1, RaceProfile(), Tech(), [planet, *extras], [])


def test_safe_high_habitability_grows_and_risky_fortifies():
    world = _planet(habitability=85, population=6_000, factories=2)
    safe = classify_planet_role(world, _state(world))
    assert safe.zone == "SAFE"
    assert safe.habit_band == "HIGH"
    assert safe.development_band == "COLONY"
    assert safe.role == "GROWTH"

    crowded = _planet(habitability=85, population=800_000, factories=400)
    crowded_role = classify_planet_role(crowded, _state(crowded))
    assert crowded_role.development_band == "DEVELOPED"
    assert crowded_role.role == "GROWTH"

    threatened = _planet(habitability=85, population=6_000)
    hostile = Planet(9, "Enemy", Position(40, 0), owner=2, observed=True, habitability=70)
    fleet = Fleet(3, "Raider", 2, Position(40, 0), role="combat")
    risky_state = GameState("role", 2450, 1, RaceProfile(), Tech(), [threatened, hostile], [fleet])
    risky = classify_planet_role(threatened, risky_state)
    assert risky.zone == "RISKY"
    assert risky.role == "FORTIFY"
    assert assign_planet_roles(risky_state, full=True)[1] == "hold_line"
    assert assign_planet_roles(_state(world), full=True)[1] != "hold_line"


def test_habit_bands_and_liveable_reds():
    average = classify_planet_role(_planet(habitability=40), _state(_planet(habitability=40)))
    assert average.habit_band == "AVERAGE"
    assert average.role == "INDUSTRIAL"

    low = classify_planet_role(_planet(habitability=12), _state(_planet(habitability=12)))
    assert low.habit_band == "LOW"
    assert low.role == "OUTPOST"

    race = RaceProfile(native={
        "hab_center": [50, 50, 50],
        "hab_low": [30, 30, 30],
        "hab_high": [70, 70, 70],
        "hab_immune": [False, False, False],
    })
    red = Planet(
        1, "Red", Position(0, 0), owner=1, observed=True, habitability=-12,
        native={"environment": [18, 50, 50], "original_environment": [18, 50, 50]},
    )
    red_state = GameState("role", 2450, 1, race, Tech(), [red], [])
    assert classify_planet_role(red, red_state).habit_band == "LOW"

    dead = Planet(
        1, "Dead", Position(0, 0), owner=1, observed=True, habitability=-15,
        native={"environment": [0, 50, 50], "original_environment": [0, 50, 50]},
    )
    dead_state = GameState("role", 2450, 1, race, Tech(), [dead], [])
    assert classify_planet_role(dead, dead_state).role == "SKIP"


def test_ironium_and_germanium_outweigh_boranium_and_surface_counts():
    germanium = _planet(
        habitability=20,
        native={"mineral_concentrations": [0, 0, 100]},
    )
    boranium = _planet(
        habitability=20,
        native={"mineral_concentrations": [0, 100, 0]},
    )
    assert mineral_value(germanium)[1] > mineral_value(boranium)[1]
    assert mineral_value(germanium)[0] == "AVERAGE"
    assert mineral_value(boranium)[0] == "LOW"

    rich = _planet(habitability=20, native={"mineral_concentrations": [100, 40, 100]})
    assert classify_planet_role(rich, _state(rich)).role == "INDUSTRIAL"

    stocked = _planet(
        habitability=20,
        ironium=8000,
        boranium=1000,
        germanium=8000,
        native={"mineral_concentrations": [20, 20, 20]},
    )
    bare = _planet(habitability=20, native={"mineral_concentrations": [20, 20, 20]})
    assert mineral_value(stocked)[1] > mineral_value(bare)[1]
    assert classify_planet_role(stocked, _state(stocked)).mineral_band == "AVERAGE"


def test_development_score_needs_population_and_factories():
    people = _planet(habitability=100, population=800_000, factories=0)
    built = _planet(habitability=100, population=800_000, factories=800)
    people_role = classify_planet_role(people, _state(people))
    built_role = classify_planet_role(built, _state(built))

    assert built_role.development_score > people_role.development_score
    assert people_role.development_band == "SOLID_POP"
    assert built_role.development_band == "DEVELOPED"

    staffed = _planet(habitability=100, population=150_000, factories=150)
    assert classify_planet_role(staffed, _state(staffed)).development_band == "COLONY"
