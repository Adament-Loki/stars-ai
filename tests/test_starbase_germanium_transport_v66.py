
from types import SimpleNamespace

from stars_ai.starbase_capabilities import starbase_capabilities
from stars_ai.planet_names import PLANET_NAMES,get_planet_name
from stars_ai.models import GameState,RaceProfile,Tech,Planet,Fleet,Position,OrderSet
from stars_ai.strategy.economy import add_economic_orders
from stars_ai.native.x_writer import _transport_unload_remainder_blocks
from stars_ai.native.player_state import PlayerState
from stars_ai.native.planet import PlanetRecord


def race():
    return RaceProfile(
        name="Test",
        primary_trait="Jack of All Trades",
        native={
            "population_efficiency_raw":10,
            "economy_raw":[10,10,10,10,5,10],
            "flags_73":0,
            "prt_id":9,
        },
    )


def test_complete_planet_name_table_and_known_ids():
    assert len(PLANET_NAMES)==999
    assert get_planet_name(473)=="Knob"
    assert get_planet_name(538)=="Magellan"
    assert get_planet_name(720)=="Quiche"
    assert get_planet_name(797)=="Serapa"
    assert get_planet_name(209)=="Crow"


def test_orbital_fort_is_not_refuel_or_shipyard():
    c=starbase_capabilities(32)
    assert c["name"]=="Orbital Fort"
    assert c["can_refuel"] is False
    assert c["can_build_ships"] is False


def test_space_dock_is_refuel_and_shipyard():
    c=starbase_capabilities(33)
    assert c["name"]=="Space Dock"
    assert c["can_refuel"] is True
    assert c["can_build_ships"] is True


def test_unknown_base_is_conservative():
    c=starbase_capabilities(99)
    assert not c["can_refuel"]
    assert not c["can_build_ships"]


def test_factory_build_is_limited_by_surface_germanium():
    p=Planet(
        0,"Home",Position(0,0),owner=1,observed=True,habitability=100,
        population=100_000,factories=20,mines=20,germanium=12,
        native={"has_starbase":False,"mineral_concentrations":[50,50,40],
                "population_raw_hundreds":1000,"population_source_year":2405},
    )
    s=GameState("g",2405,1,race(),Tech(),[p],[],native={
        "design_profiles":[],"production_by_planet":{}
    })
    o=OrderSet("g",2405,1)
    add_economic_orders(s,o,None)
    pq=next(x for x in o.orders if x.kind=="set_planet_queue")
    # 4 kT/factory with an 8 kT reserve => only one factory can be afforded.
    factories=[x for x in pq.payload["queue"] if x["item"]=="factory"]
    assert factories and factories[0]["quantity"]==1
    # Low Germanium should put mines ahead of factories.
    assert pq.payload["queue"][0]["item"]=="mine"


def test_zero_germanium_prevents_factory_build_but_can_build_mines():
    p=Planet(
        0,"Home",Position(0,0),owner=1,observed=True,habitability=100,
        population=100_000,factories=20,mines=20,germanium=0,
        native={"has_starbase":False,"mineral_concentrations":[50,50,50],
                "population_raw_hundreds":1000,"population_source_year":2405},
    )
    s=GameState("g",2405,1,race(),Tech(),[p],[],native={
        "design_profiles":[],"production_by_planet":{}
    })
    o=OrderSet("g",2405,1)
    add_economic_orders(s,o,None)
    pq=next(x for x in o.orders if x.kind=="set_planet_queue")
    assert not any(x["item"]=="factory" for x in pq.payload["queue"])
    assert any(x["item"]=="mine" for x in pq.payload["queue"])


def test_orbital_fort_planet_does_not_receive_ship_build():
    p=Planet(
        0,"FortWorld",Position(0,0),owner=1,observed=True,habitability=100,
        population=200_000,factories=200,mines=200,germanium=200,
        native={
            "has_starbase":True,
            "starbase_capabilities":starbase_capabilities(32),
            "mineral_concentrations":[50,50,50],
            "population_raw_hundreds":2000,
            "population_source_year":2405,
        },
    )
    green=Planet(1,"Green",Position(20,0),owner=None,observed=True,habitability=80)
    s=GameState("g",2405,1,race(),Tech(),[p,green],[],native={
        "design_profiles":[{
            "design_number":2,"name":"Colony","role":"colony",
            "dry_mass":60,"fuel_capacity":200,"engine_id":3,"ram_scoop":False,
            "is_starbase":False,
        }],
        "production_by_planet":{},
    })
    o=OrderSet("g",2405,1)
    add_economic_orders(s,o,None)
    assert not any(
        q.get("item")=="ship_design"
        for order in o.orders if order.kind=="set_planet_queue"
        for q in order.payload.get("queue",[])
    )


def test_known_transport_residual_uses_same_validated_unload_task():
    state=GameState(
        "g",2405,1,race(),Tech(),
        [Planet(7,"Target",Position(100,200),owner=1,observed=True)],
        [Fleet(3,"Freighter",1,Position(100,200),role="freighter",
               native={"waypoints":[
                   {"x":100,"y":200,"position_object":7,"warp":6,"task":1,"position_object_type":0x51},
                   {"x":100,"y":200,"position_object":7,"warp":6,"task":1,"position_object_type":0x51},
               ],"waypoint_count":2})],
    )
    blocks=_transport_unload_remainder_blocks(
        state,
        {"fleet_id":3,"destination_planet_id":7,"warp":6}
    )
    assert len(blocks)==1
    b=blocks[0]
    assert b.type_id==5
    assert b.data.endswith(bytes.fromhex(
        "00 20 00 20 00 20 00 20 00 70"
    ))
    # task low nibble 1 = Transport
    assert b.data[10] & 0x0F == 1


def _record(pid,turn,pop,install=True,surface=True):
    return PlanetRecord(
        planet_id=pid,owner=1,is_homeworld=False,
        is_in_use_or_robber_baron=True,has_environment_info=True,
        bit_off_for_remote_mining_and_robber_baron=True,weird_bit=False,
        has_route=False,has_surface_minerals=surface,has_artifact=False,
        has_installations=install,is_terraformed=False,has_starbase=False,
        population=pop,observed_turn=turn,
    )


def test_best_planet_prefers_newer_annual_record():
    state=PlayerState({"year":2405},"g")
    state.planets=[
        _record(0,4,5000,True,True),
        _record(0,5,5100,True,True),
    ]
    assert state.best_planets()[0].population==5100
