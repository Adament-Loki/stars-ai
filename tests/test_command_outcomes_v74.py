from types import SimpleNamespace

from stars_ai.agent import StarsAgent
from stars_ai.memory import AgentMemory
from stars_ai.models import (
    Fleet,
    GameState,
    OrderSet,
    Planet,
    Position,
    RaceProfile,
    Tech,
)
from stars_ai.native.x_writer import _build_decision_report


def _state(
    year=2400,
    *,
    fleet_position=(0,0),
    fleet=True,
    target_owner=None,
    factories=10,
    production=None,
    cargo=None,
    current_research_field=None,
    relations=None,
):
    planets=[
        Planet(
            0,"Home",Position(0,0),owner=1,population=100000,
            factories=factories,mines=10,germanium=100,
        ),
        Planet(1,"New Hope",Position(64,0),owner=target_owner,observed=True),
        Planet(2,"Far Point",Position(128,0),owner=None,observed=False),
    ]
    fleets=[]
    if fleet:
        fleets.append(Fleet(
            0,"Fleet #1",1,Position(*fleet_position),role="scout",
            native={
                "ship_count":[1]+[0]*15,
                "cargo":dict(cargo or {}),
                "waypoint_count":1,
                "waypoints":[],
            },
        ))
    race=RaceProfile(native={"player_relations":list(relations or [0,0,0])})
    return GameState(
        "GAME",year,1,race,Tech(),planets,fleets,
        native={
            "header":{"game_id":123},
            "production_by_planet":dict(production or {}),
            "current_research_field":current_research_field,
            "actual_player_relations":list(relations or [0,0,0]),
        },
    )


def test_missed_arrival_becomes_explicit_warning():
    memory=AgentMemory()
    memory.record_emitted_actions([{
        "kind":"move_fleet",
        "payload":{
            "fleet_id":0,"destination_planet_id":1,"warp":8,
            "mission":"move","native_waypoints_added":1,
        },
    }],_state())

    outcomes=memory.evaluate_action_outcomes(_state(2401))

    assert outcomes[0]["status"]=="WARNING"
    assert outcomes[0]["message"].startswith(
        "WARNING - Fleet #1 should have arrived at New Hope this turn but failed to do so"
    )
    assert memory.action_expectations


def test_arrival_and_colonization_complete_from_observable_state():
    arrival=AgentMemory()
    arrival.record_emitted_actions([{
        "kind":"move_fleet",
        "payload":{"fleet_id":0,"destination_planet_id":1,"warp":8,"mission":"move"},
    }],_state())
    assert arrival.evaluate_action_outcomes(
        _state(2401,fleet_position=(64,0))
    )[0]["status"]=="COMPLETED"
    assert not arrival.action_expectations

    colony=AgentMemory()
    colony.record_emitted_actions([{
        "kind":"colony_operation",
        "payload":{"fleet_id":0,"destination_planet_id":1,"warp":8,"mission":"colonize"},
    }],_state())
    outcome=colony.evaluate_action_outcomes(
        _state(2401,fleet=False,target_owner=1)
    )[0]
    assert outcome["status"]=="COMPLETED"
    assert "colonized New Hope" in outcome["message"]


def test_scan_arrival_requires_a_new_planet_observation():
    memory=AgentMemory()
    memory.record_emitted_actions([{
        "kind":"move_fleet",
        "payload":{
            "fleet_id":0,"destination_planet_id":1,"warp":8,"mission":"scan",
        },
    }],_state())

    arrived_without_intel=memory.evaluate_action_outcomes(
        _state(2401,fleet_position=(64,0))
    )[0]
    assert arrived_without_intel["status"]=="WARNING"
    assert "should have explored New Hope this turn but failed to do so" in arrived_without_intel["message"]

    memory.planet_intel["1"]={"last_seen_year":2401}
    observed=memory.evaluate_action_outcomes(
        _state(2401,fleet_position=(64,0))
    )[0]
    assert observed["status"]=="COMPLETED"
    assert "explored New Hope" in observed["message"]


def test_missed_colonization_uses_requested_warning_language():
    memory=AgentMemory()
    memory.record_emitted_actions([{
        "kind":"colony_operation",
        "payload":{"fleet_id":0,"destination_planet_id":1,"warp":8,"mission":"colonize"},
    }],_state())

    outcome=memory.evaluate_action_outcomes(_state(2401,fleet=False))[0]

    assert outcome["status"]=="WARNING"
    assert outcome["message"].startswith(
        "WARNING - Fleet #1 should have colonized New Hope this turn but failed to do so"
    )


def test_route_legs_are_due_sequentially_without_warning_cascade():
    memory=AgentMemory()
    memory.record_emitted_actions([{
        "kind":"move_fleet",
        "payload":{
            "fleet_id":0,"destination_planet_id":1,"warp":8,"mission":"scan",
            "route_managed":True,"native_waypoints_added":2,
            "route_waypoints":[
                {"planet_id":1,"warp":8,"task":0},
                {"planet_id":2,"warp":8,"task":0},
            ],
        },
    }],_state())

    outcomes=memory.evaluate_action_outcomes(_state(2401))

    assert [x["due_year"] for x in outcomes]==[2401,2402]
    assert [x["status"] for x in outcomes]==["WARNING","PENDING"]
    assert "earlier route leg" in outcomes[1]["message"]


def test_production_queue_completion_and_failure_are_observable():
    emitted=[{
        "kind":"set_planet_queue",
        "payload":{"planet_id":0,"queue":[{"item":"factory","quantity":5}]},
    }]
    completed=AgentMemory()
    completed.record_emitted_actions(emitted,_state())
    outcome=completed.evaluate_action_outcomes(_state(2401,factories=11))[0]
    assert outcome["status"]=="COMPLETED"

    queued=AgentMemory()
    queued.record_emitted_actions(emitted,_state())
    outcome=queued.evaluate_action_outcomes(_state(
        2401,production={"0":[{"item_name":"Factory","count":5}]},
    ))[0]
    assert outcome["status"]=="COMPLETED"

    failed=AgentMemory()
    failed.record_emitted_actions(emitted,_state())
    outcome=failed.evaluate_action_outcomes(_state(2401))[0]
    assert outcome["status"]=="WARNING"
    assert "production command should have been applied this turn" in outcome["message"]


def test_clear_queue_research_relation_and_transport_have_status_rules():
    clear=AgentMemory()
    clear.record_emitted_actions([{
        "kind":"set_planet_queue",
        "payload":{"planet_id":0,"queue":[],"clear_queue":True},
    }],_state(production={"0":[{"item_name":"Mine","count":5}]}))
    assert clear.evaluate_action_outcomes(_state(2401))[0]["status"]=="COMPLETED"

    research=AgentMemory()
    research.record_emitted_actions([{
        "kind":"set_research",
        "payload":{"field":"weapons","allocation_percent":100},
    }],_state())
    assert research.evaluate_action_outcomes(
        _state(2401,current_research_field="weapons")
    )[0]["status"]=="COMPLETED"

    relation=AgentMemory()
    relation.record_emitted_actions([{
        "kind":"set_player_relation",
        "payload":{"player_id":2,"relation":"friend"},
    }],_state())
    relation_outcome=relation.evaluate_action_outcomes(_state(2401))[0]
    assert relation_outcome["status"]=="WARNING"
    assert "Player 2 relation should have changed to friend" in relation_outcome["message"]

    transport=AgentMemory()
    transport.record_emitted_actions([{
        "kind":"transport_minerals",
        "payload":{
            "fleet_id":0,"destination_planet_id":1,"warp":8,
            "unload":{"germanium":"all"},
        },
    }],_state())
    transport_outcome=transport.evaluate_action_outcomes(
        _state(2401,fleet_position=(64,0),cargo={"germanium":5})
    )[0]
    assert transport_outcome["status"]=="WARNING"
    assert "should have unloaded at New Hope" in transport_outcome["message"]


def test_agent_notes_and_decision_report_surface_command_warning():
    memory=AgentMemory()
    memory.record_emitted_actions([{
        "kind":"move_fleet",
        "payload":{"fleet_id":0,"destination_planet_id":1,"warp":8,"mission":"move"},
    }],_state())
    state=_state(2401)
    orders=StarsAgent(state,memory=memory).play_turn()
    warning=next(x for x in orders.notes if x.startswith("WARNING - Fleet #1 should have arrived"))
    assert state.native["command_outcomes"][0]["status"]=="WARNING"

    report=_build_decision_report(
        state,
        OrderSet("GAME",2401,1),
        SimpleNamespace(fleet_intents=[],memory=memory),
        [],[],[],
    )
    assert "COMMAND OUTCOME STATUS" in report
    assert warning in report
    assert "warnings=1" in report


def test_action_ledger_round_trips_in_memory_file(tmp_path):
    memory=AgentMemory()
    memory.record_emitted_actions([{
        "kind":"move_fleet",
        "payload":{"fleet_id":0,"destination_planet_id":1,"warp":8,"mission":"move"},
    }],_state())
    path=tmp_path/"memory.json"
    memory.save(path)

    restored=AgentMemory.load(path)

    assert restored.schema_version==6
    assert restored.action_expectations==memory.action_expectations


def test_loading_old_memory_migrates_command_ledger_defaults(tmp_path):
    path=tmp_path/"old-memory.json"
    path.write_text(
        '{"schema_version":4,"game_id":123,"strategic_notes":["keep me"]}',
        encoding="utf-8",
    )

    restored=AgentMemory.load(path)

    assert restored.schema_version==6
    assert restored.strategic_notes==["keep me"]
    assert restored.action_expectations=={}
    assert restored.action_outcome_history==[]
