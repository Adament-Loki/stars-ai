from stars_ai.expansion_network import (
    evaluate_expansion_network,
    opening_target_radius,
    _stock_hub_bootstrap_cost,
)
from stars_ai.models import GameState, Planet, Position, RaceProfile, Tech


def _planet(pid, name, x, *, pop=0, owner=1, hab=80, home=False, shipyard=False, refuel=False, minerals=(0,0,0)):
    return Planet(
        pid,
        name,
        Position(x, 0),
        owner=owner,
        habitability=hab,
        population=pop,
        ironium=minerals[0],
        boranium=minerals[1],
        germanium=minerals[2],
        native={
            "is_homeworld": home,
            "has_starbase": shipyard or refuel,
            "starbase_capabilities": {
                "can_build_ships": shipyard,
                "can_refuel": refuel,
            },
        },
    )


def _state(year=2430, *, lrts=(), prt=9):
    planets = [
        _planet(0, "Home", 0, pop=500_000, home=True, shipyard=True, refuel=True, minerals=(500,500,500)),
        _planet(1, "Ring One", 160, pop=120_000, minerals=(30,20,20)),
        _planet(2, "Outer Seed", 320, pop=20_000, minerals=(5,5,5)),
        _planet(10, "Frontier A", 470, owner=None, pop=0),
        _planet(11, "Frontier B", 500, owner=None, pop=0),
    ]
    race=RaceProfile(native={"lrts":list(lrts),"prt_id":prt})
    return GameState("ring", year, 1, race, Tech(), planets, [])


def test_opening_reach_target_grows_from_300_to_500_by_turn_30():
    assert opening_target_radius(0) == 300
    assert opening_target_radius(15) == 300
    assert opening_target_radius(30) == 500
    assert 300 < opening_target_radius(20) < 500


def test_isb_bootstrap_separates_space_dock_from_optional_first_gate():
    cost = _stock_hub_bootstrap_cost(True, True)
    assert cost["base_name"] == "Space Dock"
    assert cost["gate_name"] == "Stargate 100/250"
    assert {k:cost[k] for k in ("base_resources","base_ironium","base_boranium","base_germanium")} == {
        "base_resources": 200,
        "base_ironium": 40,
        "base_boranium": 10,
        "base_germanium": 50,
    }
    assert {k:cost[k] for k in ("gate_resources","gate_ironium","gate_boranium","gate_germanium")} == {
        "gate_resources": 400,
        "gate_ironium": 100,
        "gate_boranium": 40,
        "gate_germanium": 40,
    }


def test_non_isb_bootstrap_never_uses_space_dock():
    cost = _stock_hub_bootstrap_cost(False, True)
    assert cost["base_name"] == "Space Station"
    assert cost["gate_name"] == "Stargate 100/250"
    assert cost["base_resources"] == 1200
    assert cost["base_germanium"] == 500


def test_he_bootstrap_omits_gate_but_keeps_race_legal_base():
    cost = _stock_hub_bootstrap_cost(False, False)
    assert cost["base_name"] == "Space Station"
    assert cost["gate_name"] is None


def test_ring_network_sees_population_and_hub_bootstrap_debt():
    snap = evaluate_expansion_network(_state())
    assert snap.homeworld_id == 0
    assert snap.deepest_owned_ring == 2
    assert snap.owned_radius_ly == 320
    assert snap.target_radius_ly > 300
    assert snap.population_export_backlog > 0
    assert snap.population_import_backlog > 0
    assert snap.bootstrap_germanium_deficit > 0
    assert snap.bootstrap_base_name == "Space Station"
    assert snap.expansion_network_debt is True


def test_isb_snapshot_uses_space_dock():
    snap=evaluate_expansion_network(_state(lrts=("ISB",)))
    assert snap.improved_starbases is True
    assert snap.bootstrap_base_name == "Space Dock"


def test_homeworld_uses_25_percent_opening_hold_when_good_alternate_exists():
    snap = evaluate_expansion_network(_state())
    home = next(h for h in snap.hubs if h.planet_id == 0)
    assert home.export_population == max(0, home.population - round(home.capacity * 0.25))


def test_child_world_is_not_export_source_until_about_one_third_capacity():
    snap = evaluate_expansion_network(_state())
    child = next(h for h in snap.hubs if h.planet_id == 1)
    assert child.export_population == 0
    assert child.import_population_to_25 == max(0, round(child.capacity * 0.25) - child.population)
    assert child.parent_ready is False


def test_mature_child_becomes_parent_and_export_source():
    state = _state()
    state.planets[1].population = 400_000
    state.planets[1].native["starbase_capabilities"] = {
        "can_build_ships": True,
        "can_refuel": True,
    }
    snap = evaluate_expansion_network(state)
    child = next(h for h in snap.hubs if h.planet_id == 1)
    assert child.parent_ready is True
    assert child.export_population > 0
    assert 1 in snap.parent_ready_hub_ids


def test_frontier_beyond_parent_hop_creates_range_infrastructure_need():
    snap = evaluate_expansion_network(_state())
    assert snap.frontier_worlds_inside_target >= 1
    assert snap.needs_range_infrastructure is True


def test_gate_opportunity_appears_for_non_he_when_two_hubs_are_80_to_250ly_apart():
    state = _state()
    state.planets[1].population = 300_000
    state.planets[1].native["starbase_capabilities"] = {
        "can_build_ships": True,
        "can_refuel": True,
    }
    snap = evaluate_expansion_network(state)
    assert snap.gate_pair_opportunities >= 1
    assert snap.needs_gate_network is True


def test_he_never_reports_gate_network_need():
    state=_state(prt=0)
    state.planets[1].population=300_000
    state.planets[1].native["starbase_capabilities"]={"can_build_ships":True,"can_refuel":True}
    snap=evaluate_expansion_network(state)
    assert snap.gates_available is False
    assert snap.gate_pair_opportunities == 0
    assert snap.needs_gate_network is False


def test_gate_model_never_counts_loaded_cargo_as_gate_throughput():
    state = _state()
    state.planets[1].population = 300_000
    state.planets[1].native["starbase_capabilities"] = {"can_build_ships": True, "can_refuel": True}
    snap = evaluate_expansion_network(state)
    assert snap.gate_cargo_allowed is False
    assert snap.gate_logistics_mode == "empty_ship_reposition_only"
    assert "cargo must be unloaded" in snap.to_dict()["gate_rule"]
    # Gate cost is separate from the mineral debt needed to make the child a hub.
    assert snap.bootstrap_resources == 1200
    assert snap.gate_resources == 400
