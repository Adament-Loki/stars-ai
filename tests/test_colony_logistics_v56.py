
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
    assert _manual_load_population_25kt_block(_state(),0).data==bytes.fromhex("00 00 25 00 12 08 19")

def test_exact_mineral_load():
    assert _manual_load_minerals_10_20_30_block(_state(),1).data==bytes.fromhex("01 00 25 00 12 07 0A 14 1E")

def test_colonize_sequence():
    bs=_colonize_blocks(_state(),{"fleet_id":0,"destination_planet_id":1,"warp":6,"load_25kt_population":True})
    assert [b.type_id for b in bs]==[1,4,5]
    assert bs[-1].data[10:12]==bytes.fromhex("62 51")

def test_transport_sequence():
    bs=_transport_mineral_blocks(_state(),{"fleet_id":1,"destination_planet_id":2,"warp":6})
    assert [b.type_id for b in bs]==[1,4,5]
    assert bs[-1].data[10:12]==bytes.fromhex("61 51")
    assert bs[-1].data[-10:]==bytes.fromhex(
        "00 20 00 20 00 20 00 20 00 70"
    )

def test_strategy_emits_both_operations():
    s=_state(); o=OrderSet("g",2400,1); add_economic_orders(s,o,None)
    assert any(x.kind=="colony_operation" for x in o.orders)
    assert any(x.kind=="transport_minerals" for x in o.orders)
