
from stars_ai.models import GameState,RaceProfile,Tech,Planet,Fleet,Position
from stars_ai.native.x_writer import (
    TRANSPORT_UNLOAD_ALL_LOAD_OPTIMAL,
    _transport_mineral_blocks,
)

def state():
    return GameState(
        "g",2400,1,RaceProfile(),Tech(),
        [
            Planet(0,"Source",Position(1000,1000),owner=1,observed=True),
            Planet(38,"Target",Position(1228,1568),owner=1,observed=True),
        ],
        [
            Fleet(
                3,"Swashbucker 4",1,Position(1000,1000),role="freighter",
                native={"waypoint_count":1,"waypoints":[
                    {"x":1000,"y":1000,"position_object":0,"warp":0,"task":0,"position_object_type":0x11}
                ]},
            )
        ],
    )

def test_exact_controlled_transport_directives():
    assert TRANSPORT_UNLOAD_ALL_LOAD_OPTIMAL == bytes.fromhex(
        "00 20 00 20 00 20 00 20 00 70"
    )

def test_destination_waypoint_carries_complete_transport_policy():
    blocks=_transport_mineral_blocks(
        state(),
        {"fleet_id":3,"destination_planet_id":38,"warp":5},
    )
    assert [b.type_id for b in blocks]==[1,4,5]
    task=blocks[-1]
    assert task.data[10] & 0x0F == 1
    assert task.data[-10:]==TRANSPORT_UNLOAD_ALL_LOAD_OPTIMAL

def test_new_controlled_file_confirms_20_20_20_manual_load_values():
    # GAME(2).x1 Type 1 payload for P1 Fleet #4:
    observed=bytes.fromhex("03 00 25 00 12 07 14 14 14")
    assert observed[-3:]==bytes([20,20,20])
