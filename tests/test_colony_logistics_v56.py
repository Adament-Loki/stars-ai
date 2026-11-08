
import pytest

from stars_ai.models import GameState,RaceProfile,Tech,Planet,Fleet,Position,OrderSet
from stars_ai.strategy.economy import add_economic_orders
from stars_ai.native.x_writer import _manual_load_population_25kt_block,_manual_load_minerals_10_20_30_block,_colonize_blocks,_transport_mineral_blocks

def _state():
    planets=[
        Planet(0,"Home",Position(1000,1000),owner=1,observed=True,habitability=100,population=250000,ironium=100,boranium=100,germanium=100),
        Planet(1,"Green",Position(1030,1000),owner=None,observed=True,habitability=80),
        Planet(2,"Colony",Position(1050,1000),owner=1,observed=True,habitability=70,population=30000,ironium=1,boranium=2,germanium=3)]
    fleets=[
        Fleet(0,"Colony Ship",1,Position(1000,1000),role="colony",speed=6),
        Fleet(1,"Transport",1,Position(1000,1000),role="freighter",speed=6,cargo_capacity=70,native={"cargo_capacity":70})]
    return GameState("g",2400,1,RaceProfile(),Tech(),planets,fleets)

def test_exact_population_load():
    assert _manual_load_population_25kt_block(
        _state(),0,source_planet_id=0,
    ).data==bytes.fromhex("00 00 00 00 12 08 19")

def test_exact_mineral_load():
    assert _manual_load_minerals_10_20_30_block(_state(),1).data==bytes.fromhex("01 00 25 00 12 07 0A 14 1E")

def test_colonize_sequence():
    bs=_colonize_blocks(_state(),{
        "fleet_id":0,"source_planet_id":0,"destination_planet_id":1,
        "warp":6,"load_25kt_population":True,
    })
    assert [b.type_id for b in bs]==[1,4,5]
    assert bs[0].data==bytes.fromhex("00 00 00 00 12 08 19")
    assert bs[-1].data[10:12]==bytes.fromhex("62 11")

def test_colonize_load_requires_an_explicit_source_planet():
    with pytest.raises(ValueError,match="source_planet_id"):
        _colonize_blocks(_state(),{
            "fleet_id":0,"destination_planet_id":1,"warp":6,
            "load_25kt_population":True,
        })

def test_turn_58_p2_dual_colony_orders_match_the_host_accepted_control():
    planets=[
        Planet(30,"Mayberry",Position(1126,1355),owner=2,observed=True,population=295100),
        Planet(99,"Homer",Position(1304,1455),owner=2,observed=True,population=627700),
        Planet(1,"Macintosh",Position(1020,1245),owner=None,observed=True),
        Planet(167,"Costello",Position(1541,1274),owner=None,observed=True),
    ]
    fleets=[
        Fleet(30,"Colony 30",2,Position(1126,1355),role="colony",native={"waypoint_count":1}),
        Fleet(34,"Colony 34",2,Position(1304,1455),role="colony",native={"waypoint_count":1}),
    ]
    state=GameState("latestdemo",2457,2,RaceProfile(),Tech(),planets,fleets)
    first=_colonize_blocks(state,{
        "fleet_id":30,"source_planet_id":30,"destination_planet_id":1,
        "warp":9,"load_25kt_population":True,
    })
    second=_colonize_blocks(state,{
        "fleet_id":34,"source_planet_id":99,"destination_planet_id":167,
        "warp":9,"load_25kt_population":True,
    })
    assert [block.data for block in first+second]==[
        bytes.fromhex("1e 02 1e 00 12 08 19"),
        bytes.fromhex("1e 02 01 00 fc 03 dd 04 01 00 90 11"),
        bytes.fromhex("1e 02 01 00 fc 03 dd 04 01 00 92 11"),
        bytes.fromhex("22 02 63 00 12 08 19"),
        bytes.fromhex("22 02 01 00 05 06 fa 04 a7 00 90 11"),
        bytes.fromhex("22 02 01 00 05 06 fa 04 a7 00 92 11"),
    ]

def test_transport_sequence():
    bs=_transport_mineral_blocks(_state(),{"fleet_id":1,"destination_planet_id":2,"warp":6})
    assert [b.type_id for b in bs]==[1,4,5]
    assert bs[-1].data[10:12]==bytes.fromhex("61 51")
    assert bs[-1].data[-8:]==bytes.fromhex(
        "00 20 00 20 00 20 00 20"
    )

def test_strategy_emits_colony_and_shared_transport_operations():
    s=_state(); o=OrderSet("g",2400,1); add_economic_orders(s,o,None)
    assert any(x.kind=="colony_operation" for x in o.orders)
    # The shared scheduler may correctly choose P1 population before a lower
    # ranked mineral manifest; either form is one native transport operation.
    assert any(x.kind in {"transport_population","transport_minerals"} for x in o.orders)
