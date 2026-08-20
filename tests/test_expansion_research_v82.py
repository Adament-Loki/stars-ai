from stars_ai.expansion_research import expansion_research_demands
from stars_ai.models import GameState, Planet, Position, RaceProfile, Tech


def _p(pid, x, *, owner=1, pop=0, home=False, shipyard=False, refuel=False, hab=80, minerals=(0,0,0)):
    return Planet(
        pid, f"P{pid}", Position(x,0), owner=owner, population=pop,
        habitability=hab, ironium=minerals[0], boranium=minerals[1], germanium=minerals[2],
        native={
            "is_homeworld":home,
            "starbase_capabilities":{"can_build_ships":shipyard,"can_refuel":refuel},
        },
    )


def _state(*, year=2425, construction=0, propulsion=0, prt=9, lrts=(), child_pop=300_000, child_base=True, freighter_cargo=450, bulk=False):
    race=RaceProfile(native={"prt_id":prt,"lrts":list(lrts)})
    planets=[
        _p(0,0,pop=500_000,home=True,shipyard=True,refuel=True,minerals=((1800,1000,900) if bulk else (500,500,500))),
        _p(1,160,pop=child_pop,shipyard=child_base,refuel=child_base,minerals=(20,10,10)),
        _p(2,320,pop=20_000,minerals=(5,5,5)),
        _p(10,450,owner=None),
        _p(11,490,owner=None),
    ]
    native={"design_profiles":[{
        "design_number":2,"role":"freighter","cargo_capacity":freighter_cargo,
        "fuel_capacity":210,"is_starbase":False,
    }],"production_by_planet":({"1":[{"item_type":4,"item_id":3,"count":5}]} if bulk else {})}
    return GameState("research-rings",year,1,race,Tech(construction=construction,propulsion=propulsion),planets,[],native=native)


def _by_id(ds):
    return {d["capability_id"]:d for d in ds}


def test_ife_opening_requests_named_fuel_mizer_not_generic_propulsion():
    ds=_by_id(expansion_research_demands(_state(lrts=("IFE",))))
    assert "component:fuel_mizer" in ds
    assert ds["component:fuel_mizer"]["requirements"] == {"propulsion":2}


def test_normal_race_c4_package_does_not_claim_space_dock():
    ds=_by_id(expansion_research_demands(_state()))
    d=ds["expansion:frontier_logistics_c4"]
    assert d["requirements"] == {"construction":4}
    assert "Privateer" in d["name"]
    assert "Space Dock" not in d["name"]
    assert "Space Dock" not in d["post_unlock_action"]


def test_isb_c4_package_includes_space_dock():
    ds=_by_id(expansion_research_demands(_state(lrts=("ISB",))))
    d=ds["expansion:frontier_logistics_c4"]
    assert "Privateer" in d["name"]
    assert "Space Dock" in d["name"]
    assert "Space Dock" in d["post_unlock_action"]


def test_inner_strength_c4_package_includes_fuel_transport_but_not_dock_without_isb():
    ds=_by_id(expansion_research_demands(_state(prt=4)))
    name=ds["expansion:frontier_logistics_c4"]["name"]
    assert "Fuel Transport" in name
    assert "Space Dock" not in name


def test_inner_strength_plus_isb_gets_both_special_assets():
    ds=_by_id(expansion_research_demands(_state(prt=4,lrts=("ISB",))))
    name=ds["expansion:frontier_logistics_c4"]["name"]
    assert "Fuel Transport" in name
    assert "Space Dock" in name


def test_large_freighter_is_not_triggered_by_population_backlog_alone():
    state=_state(construction=4, propulsion=5, child_pop=50_000)
    ds=_by_id(expansion_research_demands(state))
    assert "hull:2" not in ds


def test_large_freighter_is_direct_response_to_bulk_shipyard_mineral_pressure():
    state=_state(construction=4, propulsion=5, child_pop=300_000, bulk=True)
    ds=_by_id(expansion_research_demands(state))
    assert "hull:2" in ds
    assert ds["hull:2"]["requirements"] == {"construction":8}
    assert "bulk" in ds["hull:2"]["explanation"].lower()


def test_first_gate_is_exact_p5_c5_and_uses_legal_base_language():
    state=_state(construction=4,propulsion=2,child_pop=300_000,child_base=True)
    ds=_by_id(expansion_research_demands(state))
    assert "gate:100_250" in ds
    d=ds["gate:100_250"]
    assert d["requirements"] == {"propulsion":5,"construction":5}
    assert "Space Station" in d["post_unlock_action"]
    assert "Space Dock" not in d["post_unlock_action"]


def test_isb_first_gate_can_reference_space_dock_path():
    state=_state(construction=4,propulsion=2,child_pop=300_000,child_base=True,lrts=("ISB",))
    d=_by_id(expansion_research_demands(state))["gate:100_250"]
    assert "Space Dock" in d["post_unlock_action"]


def test_hyper_expansion_does_not_get_gate_research_demand():
    ds=_by_id(expansion_research_demands(_state(prt=0,construction=4,propulsion=2)))
    assert "gate:100_250" not in ds


def test_it_can_get_any_300_as_heavy_logistics_upgrade_but_normal_race_does_not():
    it=_by_id(expansion_research_demands(_state(prt=7,construction=8,propulsion=5,bulk=True)))
    normal=_by_id(expansion_research_demands(_state(prt=9,construction=8,propulsion=5)))
    assert "gate:any_300" in it
    assert "gate:any_300" not in normal


def test_no_expansion_research_is_manufactured_once_network_is_consolidated_after_turn_30():
    state=_state(year=2440,construction=13,propulsion=11,child_pop=500_000,freighter_cargo=1200)
    state.planets[2].population=400_000
    state.planets[2].native["starbase_capabilities"]={"can_build_ships":True,"can_refuel":True}
    state.planets[3].position=Position(700,0)
    state.planets[4].position=Position(750,0)
    ds=expansion_research_demands(state)
    assert ds == []


def test_gate_research_explicitly_models_empty_ship_only_repositioning():
    state=_state(construction=4,propulsion=2,child_pop=300_000,child_base=True)
    d=_by_id(expansion_research_demands(state))["gate:100_250"]
    text=(d["post_unlock_action"]+" "+d["explanation"]).lower()
    assert "empty" in text
    assert "unload" in text
    assert "loaded population/mineral freight still flies" in text
    assert "cargo" in text


def test_it_gate_does_not_claim_to_gate_loaded_heavy_freight():
    d=_by_id(expansion_research_demands(_state(prt=7,construction=8,propulsion=5,bulk=True)))["gate:any_300"]
    text=(d["post_unlock_action"]+" "+d["explanation"]).lower()
    assert "empty" in text
    assert "unload" in text
    assert "cargo" in text
