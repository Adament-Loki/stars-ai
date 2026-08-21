
from types import SimpleNamespace

from stars_ai.models import (
    GameState,RaceProfile,Tech,Planet,Fleet,Position,OrderSet
)
from stars_ai.native.race import parse_full_race_data
from stars_ai.adapters.native_core_adapter import _habitability_from_race
from stars_ai.colony_planner import score_colony_candidates
from stars_ai.strategy.research import add_research_orders
from stars_ai.native.x_writer import _manual_load_minerals_block,_transport_mineral_blocks
from stars_ai.fuel_planner import stock_hull_fuel_specs,profile_with_planned_cargo
from stars_ai.cargo_planner import derive_cargo_plan
from stars_ai.planet_economy import decode_race_economy
from stars_ai.design_development import plan_design_development
from stars_ai.native_capabilities import capability


def _full_race():
    return bytearray(0x68)


def test_humanoid_hab_bytes_decode_as_center_low_high():
    b=_full_race()
    b[8:11]=bytes([50,50,50])
    b[11:14]=bytes([15,15,15])
    b[14:17]=bytes([85,85,85])
    b[17]=15
    b[54]=10
    b[55:61]=bytes([10,10,10,10,5,10])
    b[68]=9
    r=parse_full_race_data(bytes(b))
    assert r.hab_center==(50,50,50)
    assert r.hab_low==(15,15,15)
    assert r.hab_high==(85,85,85)
    assert r.hab_immune==(False,False,False)
    assert r.universal_hab is False


def test_all_ff_hab_block_is_tri_immune_universal():
    b=_full_race()
    b[8:17]=bytes([0xFF]*9)
    b[17]=20
    b[54]=10
    b[55:61]=bytes([10,10,10,10,5,10])
    b[68]=7
    r=parse_full_race_data(bytes(b))
    assert r.hab_immune==(True,True,True)
    assert r.universal_hab is True

    p=SimpleNamespace(gravity=0,temperature=100,radiation=37)
    assert _habitability_from_race(p,r)==100


def test_normal_race_habitability_uses_actual_envelope():
    b=_full_race()
    b[8:11]=bytes([50,50,50])
    b[11:14]=bytes([15,15,15])
    b[14:17]=bytes([85,85,85])
    b[17]=15
    b[54]=10
    b[55:61]=bytes([10,10,10,10,5,10])
    b[68]=9
    r=parse_full_race_data(bytes(b))
    ideal=SimpleNamespace(gravity=50,temperature=50,radiation=50)
    red=SimpleNamespace(gravity=0,temperature=50,radiation=50)
    assert _habitability_from_race(ideal,r)>=99
    assert _habitability_from_race(red,r)<0


def test_universal_hab_colony_ranking_prefers_mineral_value_over_hab():
    race=RaceProfile(native={"universal_hab":True})
    planets=[
        Planet(0,"Home",Position(0,0),owner=1,observed=True,habitability=100),
        Planet(
            1,"Poor Rock",Position(20,0),owner=None,observed=True,habitability=100,
            native={"mineral_concentrations":[10,10,10]},
        ),
        Planet(
            2,"Rich Rock",Position(32,0),owner=None,observed=True,habitability=100,
            native={"mineral_concentrations":[90,85,80]},
        ),
    ]
    fleet=Fleet(0,"Colony",1,Position(0,0),role="colony")
    s=GameState("g",2400,1,race,Tech(),planets,[fleet])
    ranked=score_colony_candidates(s,fleet)
    assert ranked[0].planet_id==2
    assert "habitability ignored" in ranked[0].explanation


def test_post_fuel_mizer_research_only_uses_construction_energy_weapons():
    race=RaceProfile(native={"lrts":["IFE"]})
    s=GameState(
        "g",2405,1,race,
        Tech(energy=3,weapons=3,propulsion=2,construction=3,electronics=3,biotechnology=1),
        [],[],
        native={"design_profiles":[]},
    )
    o=OrderSet("g",2405,1)
    add_research_orders(s,o,None)
    r=next(x for x in o.orders if x.kind=="set_research")
    assert r.payload["field"] in {"construction","energy","weapons"}
    assert r.payload["early_mizer_doctrine"] is True
    assert "Fuel Mizer" in r.reason


def test_dynamic_small_load_encoder_accepts_calculated_values():
    state=GameState(
        "g",2400,1,RaceProfile(),Tech(),[],
        [Fleet(3,"Transport",1,Position(0,0),role="freighter",cargo_capacity=210,native={"cargo_capacity":210})],
    )
    b=_manual_load_minerals_block(
        state,3,{"ironium":37,"boranium":5,"germanium":91}
    )
    assert b.data==bytes.fromhex("03 00 25 00 12 07 25 05 5B")


def test_dynamic_small_load_rejects_unvalidated_large_single_mineral():
    state=GameState("g",2400,1,RaceProfile(),Tech(),[],[])
    try:
        _manual_load_minerals_block(
            state,0,{"ironium":0,"boranium":0,"germanium":256}
        )
    except ValueError:
        pass
    else:
        raise AssertionError("must not guess large-load encoding")


def test_stock_base_hull_cargo_capacities():
    h=stock_hull_fuel_specs()
    assert h[0].base_cargo==70
    assert h[1].base_cargo==210
    assert h[2].base_cargo==1200
    assert h[3].base_cargo==3000


def test_planned_cargo_mass_is_added_to_fuel_profile():
    fp={"dry_mass":100,"cargo_mass":0,"mass":100}
    loaded=profile_with_planned_cargo(
        fp,{"ironium":20,"boranium":30,"germanium":40}
    )
    assert loaded["cargo_mass"]==90
    assert loaded["mass"]==190


def _econ_race():
    return RaceProfile(
        native={
            "population_efficiency_raw":10,
            "economy_raw":[10,10,10,10,5,10],
            "flags_73":0,
            "prt_id":9,
        }
    )


def test_cargo_planner_derives_germanium_heavy_load_from_factory_need():
    source=Planet(
        0,"Source",Position(0,0),owner=1,observed=True,
        population=300000,factories=250,mines=250,
        ironium=200,boranium=200,germanium=220,
    )
    target=Planet(
        1,"Target",Position(20,0),owner=1,observed=True,
        population=200000,factories=50,mines=100,
        ironium=5,boranium=5,germanium=0,
    )
    fleet=Fleet(
        0,"Freighter",1,Position(0,0),role="freighter",
        cargo_capacity=70,native={"cargo_capacity":70},
    )
    orders=OrderSet("g",2400,1)
    orders.add(
        "set_planet_queue",
        {"planet_id":1,"queue":[{"item":"factory","quantity":10}]},
        "test",
    )
    plan=derive_cargo_plan(
        source,target,fleet,decode_race_economy(_econ_race()),orders
    )
    assert plan is not None
    assert plan.total<=70
    assert plan.germanium>0
    assert plan.germanium>=plan.ironium
    assert plan.germanium>=plan.boranium


def test_transport_writer_uses_order_specific_load_and_full_unload_policy():
    state=GameState(
        "g",2400,1,RaceProfile(),Tech(),
        [
            Planet(0,"Source",Position(0,0),owner=1,observed=True),
            Planet(1,"Target",Position(20,0),owner=1,observed=True),
        ],
        [
            Fleet(
                0,"Transport",1,Position(0,0),role="freighter",
                cargo_capacity=210,
                native={"cargo_capacity":210,"waypoint_count":1,"waypoints":[]},
            )
        ],
    )
    bs=_transport_mineral_blocks(
        state,
        {
            "fleet_id":0,
            "destination_planet_id":1,
            "warp":5,
            "load":{"ironium":11,"boranium":22,"germanium":33},
        },
    )
    assert bs[0].data.endswith(bytes([11,22,33]))
    assert bs[-1].data.endswith(bytes.fromhex(
        "00 20 00 20 00 20 00 20 00 70"
    ))


def _design_state():
    race=RaceProfile(native={"lrts":["IFE","ISB"],"universal_hab":False})
    planets=[
        Planet(
            0,"Home",Position(0,0),owner=1,observed=True,population=300000,
            native={
                "starbase_capabilities":{
                    "can_build_ships":False,
                    "can_refuel":False,
                    "is_orbital_fort":True,
                }
            },
        ),
        Planet(1,"Unknown",Position(30,0),owner=None,observed=False),
    ]
    native={
        "design_profiles":[
            {
                "design_number":0,"name":"Old Scout","role":"scout",
                "hull_id":4,"dry_mass":25,"cargo_capacity":0,
                "fuel_capacity":50,"engine_id":3,"engine_name":"Long Hump 6",
            },
            {
                "design_number":1,"name":"Small Freighter","role":"freighter",
                "hull_id":0,"dry_mass":35,"cargo_capacity":70,
                "fuel_capacity":130,"engine_id":3,"engine_name":"Long Hump 6",
            },
        ],
        "starbase_profiles":[
            {
                "design_number":0,"name":"Orbital Fort","hull_id":32,
                "hull_name":"Orbital Fort",
            }
        ],
        "designs":[],
    }
    return GameState(
        "g",2405,1,race,
        Tech(energy=4,weapons=4,propulsion=2,construction=4,electronics=4,biotechnology=2),
        planets,[],native=native,
    )


def test_design_planner_does_not_propose_unproven_scout_and_still_proposes_real_space_dock():
    proposals=plan_design_development(_design_state())
    names={p.name for p in proposals}
    # v8.6 requires a material mission-performance improvement and exact raw
    # current-design slots before proposing a native scout clone.
    assert "Long Range Scout Mk II" not in names
    assert "Fleet Support Space Dock" in names
    dock=next(p for p in proposals if p.name=="Fleet Support Space Dock")
    assert dock.desired_hull_id==33
    assert dock.is_starbase is True


def test_native_design_capabilities_are_explicitly_split():
    assert capability("create_design").status=="PARTIAL"  # generic ship and starbase compiler
    assert capability("create_ship_design").status=="PARTIAL"
    assert capability("delete_ship_design").status=="PARTIAL"
    assert capability("replace_ship_design").status=="BLOCKED"
