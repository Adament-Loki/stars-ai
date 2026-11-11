"""Named stock-tech design fixtures and combat-line architecture rules."""
from __future__ import annotations

from stars_ai.models import GameState, RaceProfile, Tech
from stars_ai.ship_design_synth import synthesize_role_design
from stars_ai.starsapi_items import BEAM, ELECTRICAL, MECHANICAL, TORPEDO, stock_component_database


def _state(tech: Tech, *, enemy_rows=None) -> GameState:
    return GameState(
        "golden-designs", 2405, 1,
        RaceProfile(native={"prt_id": 9, "lrts": []}), tech, [], [],
        native={
            "designs": [], "design_profiles": [], "production_by_planet": {},
            "enemy_design_profiles": list(enemy_rows or []),
        },
    )


def _fitted_counts(plan) -> dict[str, int]:
    names = {
        (spec.category, spec.item_id): spec.name
        for spec in stock_component_database().components.values()
    }
    result: dict[str, int] = {}
    for component in plan.encoded.slots:
        key = (component.category, component.item_id)
        if component.count <= 0 or key not in names:
            continue
        result[names[key]] = result.get(names[key], 0) + component.count
    return result


def _combat_categories(plan) -> set[int]:
    return {
        component.category for component in plan.encoded.slots
        if component.count > 0 and component.category in (BEAM, TORPEDO)
    }


def test_low_torpedo_destroyer_is_a_legal_stinger_style_package():
    plan = synthesize_role_design(_state(Tech(energy=2, weapons=5, propulsion=3, construction=3)), "combat")
    assert plan is not None
    assert plan.encoded.hull_id == 6  # Destroyer
    fitted = _fitted_counts(plan)
    assert fitted["Long Hump 6"] == 1
    assert fitted["Beta Torpedo"] == 3
    assert fitted["Maneuvering Jet"] == 1
    assert fitted["Battle Computer"] == 1
    assert _combat_categories(plan) == {TORPEDO}


def test_low_beam_destroyer_is_a_legal_razor_style_package():
    plan = synthesize_role_design(_state(Tech(energy=2, weapons=8, propulsion=3, construction=3)), "combat")
    assert plan is not None
    assert plan.encoded.hull_id == 6  # Destroyer
    fitted = _fitted_counts(plan)
    assert fitted["Long Hump 6"] == 1
    assert fitted["Phaser Bazooka"] == 3
    assert fitted["Maneuvering Jet"] == 1
    assert fitted["Battle Computer"] == 1
    assert _combat_categories(plan) == {BEAM}


def test_range_three_cruiser_uses_beam_capacitor_and_mobility_package():
    plan = synthesize_role_design(
        _state(Tech(energy=7, weapons=10, propulsion=12, construction=9, electronics=4)),
        "combat",
    )
    assert plan is not None
    assert plan.encoded.hull_id == 7  # Cruiser
    fitted = _fitted_counts(plan)
    assert fitted["Colloidal Phaser"] == 6
    assert fitted["Energy Capacitor"] == 1
    assert fitted["Overthruster"] == 1
    assert fitted["Wolverine Diffuse Shield"] >= 2
    assert _combat_categories(plan) == {BEAM}


def test_weapons_twelve_battleship_stays_a_beam_line_instead_of_a_missile_dump():
    plan = synthesize_role_design(
        _state(Tech(energy=6, weapons=12, propulsion=6, construction=13)),
        "combat",
    )
    assert plan is not None
    assert plan.encoded.hull_id == 9  # Battleship
    fitted = _fitted_counts(plan)
    assert "Jihad Missile" not in fitted
    assert _combat_categories(plan) == {BEAM}
    assert sum(count for name, count in fitted.items() if "Phaser" in name or "Blaster" in name or "Disruptor" in name) >= 6


def test_enemy_beam_swarm_and_missile_stack_both_stay_on_a_pure_beam_line():
    tech = Tech(energy=6, weapons=12, propulsion=6, construction=13)
    anti_missile = synthesize_role_design(
        _state(tech, enemy_rows=[{"torpedo_strength": 50, "beam_strength": 5}]), "combat",
    )
    against_destroyers = synthesize_role_design(
        _state(tech, enemy_rows=[{"torpedo_strength": 5, "beam_strength": 50}]), "combat",
    )
    assert anti_missile is not None and against_destroyers is not None
    assert _combat_categories(anti_missile) == {BEAM}
    assert _combat_categories(against_destroyers) == {BEAM}


def test_observed_missile_pressure_keeps_a_researched_jammer_in_the_beam_counter():
    plan = synthesize_role_design(
        _state(
            Tech(energy=4, weapons=12, propulsion=6, construction=13, electronics=10),
            enemy_rows=[{"torpedo_strength": 50, "beam_strength": 1}],
        ),
        "combat",
    )
    assert plan is not None
    fitted = _fitted_counts(plan)
    assert fitted["Jammer 20"] == 1
    assert _combat_categories(plan) == {BEAM}
