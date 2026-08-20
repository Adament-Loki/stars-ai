from pathlib import Path

from stars_ai.adapters.stars_native import NativeBlock, read_blocks
from stars_ai.memory import AgentMemory
from stars_ai.models import Fleet,GameState,Planet,Position,RaceProfile,Tech
from stars_ai.native.player_state import PlayerState
from stars_ai.native.x_writer import (
    _movement_route_blocks,
    _waypoint_change_task_block,
)
from stars_ai.windows_autohost import IntegratedNativeOrderBridge


def _route_state():
    destinations=[
        (85,"Resort",1369,1298,8),
        (82,"McIntyre",1342,1240,8),
        (59,"Allen",1274,1212,7),
        (62,"Dollar",1284,1191,5),
        (65,"Bagnose",1298,1181,5),
        (70,"Morgan",1305,1128,8),
        (57,"Hell",1271,1136,6),
    ]
    planets=[
        Planet(pid,name,Position(x,y),observed=False)
        for pid,name,x,y,_ in destinations
    ]
    fleet=Fleet(
        0,"Probe",1,Position(1311,1281),role="scout",
        native={"waypoint_count":1,"waypoints":[]},
    )
    state=GameState("GAME",2400,1,RaceProfile(),Tech(),planets,[fleet])
    specs=[
        {"planet_id":pid,"warp":warp,"task":0}
        for pid,_,_,_,warp in destinations
    ]
    return state,specs


def test_seven_leg_route_emits_sequential_waypoint_adds():
    state,specs=_route_state()
    blocks=_movement_route_blocks(
        state,
        {
            "fleet_id":0,
            "destination_planet_id":85,
            "warp":8,
            "mission":"scan",
            "route_managed":True,
            "route_waypoints":specs,
        },
    )

    assert [b.type_id for b in blocks]==[4]*7
    assert [int.from_bytes(b.data[2:4],"little") for b in blocks]==list(range(1,8))
    assert [int.from_bytes(b.data[8:10],"little") for b in blocks]==[85,82,59,62,65,70,57]
    assert [b.data[10] >> 4 for b in blocks]==[8,8,7,5,5,8,6]


def test_task_change_can_address_later_waypoint_index():
    state,_=_route_state()
    block=_waypoint_change_task_block(
        state,
        fleet_id=0,
        destination_planet_id=70,
        warp=8,
        task=0,
        object_type=0x11,
        waypoint_index=6,
    )
    assert block.type_id==5
    assert int.from_bytes(block.data[2:4],"little")==6


def test_type19_waypoint_task_occupies_fleet_waypoint_slot(monkeypatch):
    header,blocks,_=read_blocks(Path("playtests/seed/TwoAIOneComp/GAME.m1"))
    first_wp=next(i for i,b in enumerate(blocks) if b.type_id==20)
    data=bytearray(blocks[first_wp].data)
    data[4:6]=(85).to_bytes(2,"little")
    data[6]=(7 << 4) | 2
    blocks[first_wp]=NativeBlock(19,len(data),bytes(data))

    monkeypatch.setattr(
        "stars_ai.native.player_state.read_blocks",
        lambda _: (header,blocks,None),
    )
    state=PlayerState.from_files("synthetic.m1")

    first_fleet=state.fleets[0]
    waypoint=state.waypoints_by_fleet[first_fleet.fleet_id][0]
    assert waypoint.position_object==85
    assert waypoint.waypoint_task==2


def test_native_memory_is_promoted_only_on_commit(tmp_path):
    bridge=IntegratedNativeOrderBridge(memory_root=tmp_path)
    committed=tmp_path/"player-01-memory.json"
    pending=tmp_path/"player-01-memory.pending.json"
    committed.write_text("old",encoding="utf-8")
    pending.write_text("discard",encoding="utf-8")
    bridge._pending_memories[1]=(committed,pending)
    bridge.discard_pending_memory()
    assert committed.read_text(encoding="utf-8")=="old"
    assert not pending.exists()

    pending.write_text("accepted",encoding="utf-8")
    bridge._pending_memories[1]=(committed,pending)
    bridge.commit_pending_memory()
    assert committed.read_text(encoding="utf-8")=="accepted"


def test_scan_memory_records_only_emitted_native_routes():
    memory=AgentMemory()
    payload={
        "fleet_id":0,
        "destination_planet_id":85,
        "mission":"scan",
        "route_managed":True,
        "route_planet_ids":[85,82],
        "route_waypoints":[
            {"planet_id":85,"warp":8,"task":0},
            {"planet_id":82,"warp":7,"task":0},
        ],
    }
    memory.record_emitted_scan_orders([],2400)
    assert memory.scout_routes=={}
    memory.record_emitted_scan_orders(
        [{"kind":"move_fleet","payload":payload}],2400
    )
    assert memory.scout_routes["0"]["planet_ids"]==[85,82]
    assert memory.scan_target_history["85"]["assignment_count"]==1
