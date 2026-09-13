"""Regression boundaries for the active stock MOD and hull catalog.

These tests intentionally name only the game-data thresholds.  They verify
that a future catalogue/parser change cannot turn a published strategic
milestone into an AI assumption.
"""
from __future__ import annotations

import pytest

from stars_ai.models import GameState, RaceProfile, Tech
from stars_ai.starsapi_items import (
    component_spec,
    mystery_trader_component_bits,
    researched_available_components,
    stock_component_database,
    stock_hulls,
    hull_unlocked,
)
from stars_ai.combat_doctrine import weapon_stats


def _state(*, tech: Tech, mt_mask: int = 0, known_components=None) -> GameState:
    native = {}
    if known_components is not None:
        native["known_components"] = known_components
    return GameState(
        "catalog-boundaries", 2400, 1,
        RaceProfile(native={"prt_id": 9, "lrts": [], "mt_mask": mt_mask}),
        tech, [], [], native=native,
    )


def _component_key(name: str) -> tuple[int, int]:
    return next(
        key for key, spec in stock_component_database().components.items()
        if spec.name == name
    )


@pytest.mark.parametrize(
    ("name", "below", "at_threshold"),
    [
        ("Beta Torpedo", Tech(weapons=4, propulsion=1), Tech(weapons=5, propulsion=1)),
        ("Phaser Bazooka", Tech(weapons=7), Tech(weapons=8)),
        ("Colloidal Phaser", Tech(weapons=9), Tech(weapons=10)),
        ("Jihad Missile", Tech(weapons=12, propulsion=5), Tech(weapons=12, propulsion=6)),
        ("Juggernaut Missile", Tech(weapons=15, propulsion=8), Tech(weapons=16, propulsion=8)),
        ("Armageddon Missile", Tech(weapons=24, propulsion=9), Tech(weapons=24, propulsion=10)),
    ],
)
def test_component_technology_boundaries_are_read_from_active_catalog(name, below, at_threshold):
    key = _component_key(name)
    assert key not in researched_available_components(_state(tech=below))
    assert key in researched_available_components(_state(tech=at_threshold))


@pytest.mark.parametrize(
    ("hull_name", "below", "at_threshold"),
    [
        ("Battleship", Tech(construction=12), Tech(construction=13)),
        ("Nubian", Tech(construction=25), Tech(construction=26)),
    ],
)
def test_hull_technology_boundaries_are_read_from_active_catalog(hull_name, below, at_threshold):
    hull = next(hull for hull in stock_hulls().values() if hull.name == hull_name)
    assert not hull_unlocked(hull, _state(tech=below))
    assert hull_unlocked(hull, _state(tech=at_threshold))


def test_mystery_trader_components_stay_locked_at_their_tech_threshold():
    # This covers the three MT items most likely to look ordinary to a generic
    # fitter, as well as the mapping used by native player-record mt_mask.
    state = _state(tech=Tech(
        energy=30, weapons=30, propulsion=30, construction=30,
        electronics=30, biotechnology=30,
    ))
    available = researched_available_components(state)
    names = ("Multi Function Pod", "Multi Cargo Pod", "Anti Matter Torpedo")
    assert all(_component_key(name) not in available for name in names)

    bits = mystery_trader_component_bits()
    assert {
        _component_key("Multi Cargo Pod"): 0,
        _component_key("Multi Function Pod"): 1,
        _component_key("Anti Matter Torpedo"): 6,
    }.items() <= bits.items()


@pytest.mark.parametrize("name", ["Multi Cargo Pod", "Multi Function Pod", "Anti Matter Torpedo"])
def test_acquired_mystery_trader_component_is_available_only_with_its_flag(name):
    key = _component_key(name)
    bit = mystery_trader_component_bits()[key]
    high = Tech(energy=30, weapons=30, propulsion=30, construction=30, electronics=30, biotechnology=30)
    available = researched_available_components(_state(tech=high, mt_mask=1 << bit))
    assert key in available


def test_normalized_known_component_overlay_can_represent_a_confirmed_acquisition():
    key = _component_key("Multi Function Pod")
    state = _state(
        tech=Tech(energy=11, propulsion=11, construction=11, electronics=11),
        known_components=["Multi Function Pod"],
    )
    assert key in researched_available_components(state)


def test_catalog_preserves_six_field_requirements_base_costs_and_battle_statistics():
    beta_key = _component_key("Beta Torpedo")
    beta = component_spec(*beta_key)
    assert beta.tech_required == (0, 5, 1, 0, 0, 0)
    assert (beta.resource_cost, beta.ironium, beta.boranium, beta.germanium) == (6, 18, 6, 4)
    beta_battle = weapon_stats(*beta_key)
    assert beta_battle is not None
    assert (beta_battle.weapon_range, beta_battle.damage, beta_battle.initiative, beta_battle.accuracy) == (4, 12, 1, 0.45)

    jihad_key = _component_key("Jihad Missile")
    jihad = component_spec(*jihad_key)
    assert jihad.tech_required == (0, 12, 6, 0, 0, 0)
    assert (jihad.resource_cost, jihad.ironium, jihad.boranium, jihad.germanium) == (13, 37, 13, 9)
    jihad_battle = weapon_stats(*jihad_key)
    assert jihad_battle is not None
    assert (jihad_battle.weapon_range, jihad_battle.damage, jihad_battle.initiative, jihad_battle.accuracy) == (5, 85, 0, 0.20)
