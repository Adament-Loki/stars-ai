from stars_ai.adapters.native_core_adapter import _normalize_active_waypoint
from stars_ai.memory import AgentMemory
from stars_ai.models import Fleet,GameState,OrderSet,Planet,Position,RaceProfile,Tech
from stars_ai.native.waypoint import WaypointRecord
from stars_ai.native.x_writer import _movement_route_blocks, _native_waypoint_decision
from stars_ai.warp_policy import optimize_active_route_warps


def _waypoint(*, destination, warp, task, object_type=0x91):
    return WaypointRecord(
        x=1100,
        y=1000,
        position_object=destination,
        warp=warp,
        waypoint_task=task,
        position_object_type=object_type,
    )


def test_native_planet_waypoint_with_upper_bits_is_normalized():
    current=_waypoint(destination=0,warp=0,task=0,object_type=0x11)
    active=_waypoint(destination=7,warp=7,task=2,object_type=0x91)
    normalized=_normalize_active_waypoint([current,active])
    assert normalized=={
        "destination_planet_id":7,
        "destination_warp":7,
        "destination_task":2,
        "destination_mission":"colonize",
    }


def _state(*, x=1000, year=2400, destination=1, task=0, warp=7):
    native_waypoints=[
        {"position_object_type":0x11,"position_object":0,"warp":0,"task":0},
        {
            "position_object_type":0x91,
            "position_object":destination,
            "warp":warp,
            "task":task,
        },
    ]
    return GameState(
        "g",year,1,RaceProfile(),Tech(),
        [
            Planet(0,"Home",Position(1000,1000),owner=1),
            Planet(1,"Target",Position(1100,1000)),
            Planet(2,"Other",Position(1200,1000)),
        ],
        [
            Fleet(
                0,"Scout",1,Position(x,1000),destination_planet_id=destination,
                role="scout",native={
                    "waypoint_count":2,
                    "waypoints":native_waypoints,
                    "native_destination_planet_id":destination,
                    "native_destination_warp":warp,
                    "native_destination_task":task,
                },
                destination_warp=warp,
                destination_task=task,
                destination_mission="move" if task==0 else None,
            )
        ],
        native={"header":{"game_id":123}},
    )


def test_identical_native_mission_is_continue():
    decision=_native_waypoint_decision(
        _state(),
        {"fleet_id":0,"destination_planet_id":1,"warp":7,"mission":"recon"},
        operation_kind="move_fleet",
    )
    assert decision["result"]=="CONTINUE"
    assert decision["native_waypoint_destination"]==1
    assert decision["native_waypoint_warp"]==7
    assert decision["native_waypoint_task"]==0


def test_same_destination_task_with_new_warp_emits_type5_update():
    state=_state(warp=7)
    payload={"fleet_id":0,"destination_planet_id":1,"warp":9,"mission":"recon"}
    decision=_native_waypoint_decision(state,payload,operation_kind="move_fleet")
    assert decision["result"]=="UPDATE WARP"
    blocks=_movement_route_blocks(state,payload)
    assert len(blocks)==1
    assert blocks[0].type_id==5
    assert int.from_bytes(blocks[0].data[2:4],"little")==1
    assert blocks[0].data[10]>>4==9
    assert blocks[0].data[11]==0x91


def test_active_native_waypoint_is_reoptimized_each_turn():
    state=_state(warp=5)
    fleet=state.fleets[0]
    fleet.native["fuel_profile"]={
        "fuel":100,"effective_fuel":100,"fuel_capacity":100,
        "groups":[{"engine_id":2,"mass":6,"engine_name":"Fuel Mizer"}],
    }
    fleet.native["race_fuel_flags"]={"ife":True,"ce":False}
    orders=OrderSet("g",2400,1)
    assert optimize_active_route_warps(state,orders)==1
    update=orders.orders[0]
    assert update.kind=="move_fleet"
    assert update.payload["warp_reoptimization"] is True
    assert update.payload["warp"]==9


def test_different_native_destination_is_blocked_retarget():
    decision=_native_waypoint_decision(
        _state(),
        {"fleet_id":0,"destination_planet_id":2,"warp":7,"mission":"recon"},
        operation_kind="move_fleet",
    )
    assert decision["result"]=="BLOCKED RETARGET"
    assert "disabled" in decision["reason"]


def test_repeated_warp_seven_progress_near_four_ly_is_flagged():
    memory=AgentMemory()
    first=memory.update_movement_progress(_state(x=1000,year=2400))
    second=memory.update_movement_progress(_state(x=1004,year=2401))
    third=memory.update_movement_progress(_state(x=1008,year=2402))
    assert first[0]["actual_progress"] is None
    assert second[0]["actual_progress"]==4.0
    assert second[0]["suspicious_slow_turns"]==1
    assert third[0]["suspicious_slow_turns"]==2
    assert "SUSPICIOUS MOVEMENT" in third[0]["flag"]
