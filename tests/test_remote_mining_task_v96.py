from stars_ai.fleet_intent import ensure_fleet_activity
from stars_ai.models import Fleet, GameState, OrderSet, Planet, Position, RaceProfile, Tech
from stars_ai.native.x_writer import _remote_mining_task_block


def _state(*, task=0):
    target=Planet(
        98,"Sheridan",Position(1426,2023),owner=None,observed=True,
        native={"mineral_concentrations":[100,100,100]},
    )
    miner=Fleet(
        8,"Remote Miner",2,Position(1426,2023),role="miner",
        native={
            "position_object_id":98,
            "waypoint_count":1,
            "waypoints":[{
                "position_object":98,"warp":0,"task":task,
                "position_object_type":0x11,
            }],
        },
    )
    return GameState("remote",2450,2,RaceProfile(),Tech(),[target],[miner])


def test_remote_mining_task_reproduces_sandbox_x2_client_block():
    state=_state()
    block=_remote_mining_task_block(state,{
        "fleet_id":8,"destination_planet_id":98,"mission":"remote_mine",
    })
    assert block is not None
    assert block.type_id == 5
    # sandbox/GAME.x2, Player 2 fleet #9 (zero-based id 8), Sheridan id 98.
    assert block.data == bytes.fromhex("08 02 00 00 92 05 e7 07 62 00 03 11")


def test_miner_at_target_requests_remote_mining_task_not_another_move():
    state=_state()
    orders=OrderSet(state.game_name,state.year,state.player_id)
    intents=ensure_fleet_activity(state,orders)
    order=next(order for order in orders.orders if order.payload.get("fleet_id") == 8)
    assert order.kind == "remote_mine"
    assert order.payload["mission"] == "remote_mine"
    assert next(intent for intent in intents if intent["fleet_id"] == 8)["action"] == "REMOTE MINE"


def test_existing_remote_mining_task_is_idempotent():
    state=_state(task=3)
    assert _remote_mining_task_block(state,{
        "fleet_id":8,"destination_planet_id":98,"mission":"remote_mine",
    }) is None
