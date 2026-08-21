from stars_ai.design_legality import ComponentCategory, ComponentRef
from stars_ai.models import GameState, Planet, Position, RaceProfile, Tech
from stars_ai.native.design_change import create_ship_design_blocks
from stars_ai.ship_design_synth import synthesize_generic_design_proposal
from stars_ai.starsapi_items import (
    ELECTRICAL, ENGINE, MECHANICAL, MINE_LAYER, MINING_ROBOT, SCANNER, SHIELD,
    researched_available_components,
)


def _state(*, prt=9, lrts=(), tech=None, designs=None):
    return GameState(
        "availability", 2405, 2,
        RaceProfile(native={"prt_id": prt, "lrts": list(lrts)}),
        tech or Tech(),
        [Planet(0, "Home", Position(0, 0), owner=2, habitability=100)], [],
        native={
            "designs": list(designs or []),
            "design_profiles": [{"role": "colony", "engine_id": 4}] if designs else [],
            "production_by_planet": {},
        },
    )


def _available(state):
    return set(researched_available_components(state))


def test_ife_fuel_mizer_uses_official_trait_gate_and_mod_research_requirement():
    state = _state(lrts=("IFE",), tech=Tech(propulsion=2))
    available = _available(state)
    assert (ENGINE, 2) in available  # Fuel Mizer: IFE + Propulsion 2.
    assert (ENGINE, 15) not in available  # Galaxy Scoop still needs Propulsion 20.
    assert (ENGINE, 0) not in available  # HE-only Settler's Delight.

    no_ife = _available(_state(tech=Tech(propulsion=20)))
    assert (ENGINE, 2) not in no_ife


def test_nrse_and_obrm_remove_the_officially_forbidden_components():
    nrse = _available(_state(lrts=("NRSE",), tech=Tech(propulsion=20)))
    assert (ENGINE, 7) in nrse  # NRSE grants Interspace-10.
    assert all((ENGINE, item_id) not in nrse for item_id in range(10, 16))

    obrm = _available(_state(
        lrts=("OBRM",),
        tech=Tech(energy=8, construction=12, electronics=6),
    ))
    assert (MINING_ROBOT, 1) in obrm  # Robo-Mini-Miner remains legal.
    assert all((MINING_ROBOT, item_id) not in obrm for item_id in (2, 3, 4))


def test_prt_and_nas_component_gates_are_checked_before_design_synthesis():
    high = Tech(energy=30, weapons=30, propulsion=30, construction=30, electronics=30, biotechnology=30)
    ss = _available(_state(prt=1, tech=high))
    assert {(SCANNER, 5), (SCANNER, 6), (SCANNER, 14), (SHIELD, 4)} <= ss

    non_ss = _available(_state(prt=9, tech=high))
    assert not {(SCANNER, 5), (SCANNER, 6), (SCANNER, 14), (SHIELD, 4)} & non_ss

    nas = _available(_state(prt=9, lrts=("NAS",), tech=high))
    assert (SCANNER, 7) not in nas  # Ferret: a standard penetrating scanner.
    assert (SCANNER, 13) in nas     # Eagle Eye: normal scanner remains legal.


def test_standard_mine_dispensers_are_general_but_heavy_sd_equipment_is_trait_gated():
    standard = _available(_state(prt=3, tech=Tech()))
    assert (MINE_LAYER, 0) in standard  # Mine Dispenser 40 fits compatible general hulls.

    high = Tech(energy=10, weapons=10, propulsion=10, construction=10, electronics=10, biotechnology=10)
    non_sd = _available(_state(prt=3, tech=high))
    sd = _available(_state(prt=5, tech=high))
    assert (MINE_LAYER, 4) not in non_sd  # Heavy Dispenser 50 is SD-only.
    assert (MINE_LAYER, 4) in sd


def test_generic_colonizer_is_compiled_only_with_researched_race_legal_parts():
    existing = {
        "design_number": 0,
        "name": "Mayflower",
        "hull_id": 15,
        "is_starbase": False,
        "slots": [
            {"category": ENGINE, "item_id": 4, "count": 1},
            {"category": MECHANICAL, "item_id": 0, "count": 1},
        ],
    }
    state = _state(prt=7, lrts=("IFE",), tech=Tech(propulsion=2, construction=10), designs=[existing])
    payload = {
        "role": "colony",
        "name": "Colonizer Mk II",
        "is_starbase": False,
        "desired_hull_id": 15,
        "desired_hull_name": "Colony Ship",
        "desired_engine": "Fuel Mizer",
        "priority": 116,
    }
    plan = synthesize_generic_design_proposal(state, payload)
    assert plan is not None
    assert plan.encoded.name == "Colonizer Mk II"
    assert plan.encoded.staging_name == "Colony Ship"
    assert plan.encoded.slots == (
        ComponentRef(ENGINE, 2, 1),
        ComponentRef(MECHANICAL, 0, 1),
    )

    blocks = create_ship_design_blocks(plan.encoded, player_id=2)
    assert [block.data[:2] for block in blocks] == [bytes.fromhex("11 A1"), bytes.fromhex("11 A1")]

    payload["desired_engine"] = "Galaxy Scoop"
    assert synthesize_generic_design_proposal(state, payload) is None
