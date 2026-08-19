
from dataclasses import dataclass
from stars_ai.territorial_value import (
    estimate_planet_investment,
    assess_defense_posture,
)


@dataclass
class P:
    owner_id: int
    x: int
    y: int
    population: int = 0
    factories: int = 0
    mines: int = 0
    defenses: int = 0
    has_starbase: bool = False
    has_scanner: bool = False
    is_homeworld: bool = False
    ironium: int = 0
    boranium: int = 0
    germanium: int = 0
    ironium_conc: int = 50
    boranium_conc: int = 50
    germanium_conc: int = 50


class S:
    player_id = 1
    def __init__(self, planets):
        self.planets = planets


def test_remote_low_population_colony_is_abandonable():
    hw = P(1, 0, 0, population=500000, factories=300, mines=250, defenses=80, has_starbase=True, is_homeworld=True)
    remote = P(1, 400, 400, population=5000, factories=5, mines=5)
    state = S([hw, remote])

    inv = estimate_planet_investment(state, remote)
    posture = assess_defense_posture(state, remote, attack_strength=0.6, attacker_hostility=0.5)

    assert inv.total_value < 0.4
    assert posture.abandonability > 0.5
    assert posture.escalation_priority < 0.4
    assert posture.recommended_response in ("LIMITED_RESPONSE_OR_ABANDON", "CONTAIN_AND_MONITOR")


def test_core_homeworld_attack_is_existential_priority():
    hw = P(1, 0, 0, population=700000, factories=500, mines=350, defenses=100, has_starbase=True, has_scanner=True, is_homeworld=True)
    fringe = P(1, 120, 20, population=15000, factories=20, mines=25)
    state = S([hw, fringe])

    inv = estimate_planet_investment(state, hw)
    posture = assess_defense_posture(state, hw, attack_strength=0.7, attacker_hostility=0.7)

    assert inv.total_value > 0.7
    assert posture.defense_priority > 0.7
    assert posture.escalation_priority > 0.5
    assert posture.recommended_response == "DEFEND_AT_HIGH_PRIORITY"


def test_developed_non_homeworld_can_be_high_value():
    hw = P(1, 0, 0, population=500000, factories=300, mines=250, is_homeworld=True)
    core2 = P(1, 30, 30, population=400000, factories=350, mines=300, defenses=60, has_starbase=True)
    state = S([hw, core2])

    inv = estimate_planet_investment(state, core2)
    assert inv.total_value > 0.6
    assert inv.core_proximity_value > 0.7
