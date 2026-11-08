"""Phase 5 contracts for transactional persistent AI state and services."""

from __future__ import annotations

from dataclasses import asdict
import json

from stars_ai.ai.state_store import AIStateStore
from stars_ai.memory import AgentMemory
from stars_ai.memory_services import (
    action_outcome_service,
    exploration_memory_service,
    intelligence_memory_service,
    kpi_history_service,
    movement_audit_service,
)
from stars_ai.models import Fleet, GameState, Planet, Position, RaceProfile, Tech


def _memory() -> AgentMemory:
    return AgentMemory(
        game_id=77,
        player_id=1,
        start_year=2400,
        last_year=2410,
        known_enemy_planets={"8": 2},
        planet_scores={"8": 67.5},
        strategic_notes=["old note"],
        goal_progress={"expand": 0.5},
        diplomacy={"2": {"attitude": "hostile"}},
        initial_owned_planet_ids=[0],
        planet_intel={"8": {"name": "Triton", "ever_observed": True}},
        colonized_years={"0": 2400},
        scan_target_history={"8": {"last_assigned_year": 2410}},
        scout_routes={"3": {"planet_ids": [8]}},
        kpi_history=[{"year": 2410, "owned_planets": 1}],
        fleet_movement_history={"3": {"range": 12.0}},
        action_expectations={"move:3": {"due_year": 2411}},
        action_outcome_history=[{"action_id": "move:3", "status": "PENDING"}],
        research_state={"capability_id": "propulsion_3"},
        research_history=[{"year": 2410, "posture": "SPRINT"}],
        territorial_exposure_history={"pressure_reserve": 1.5},
        capability_execution={"design:7": {"status": "HOST_CONFIRMED"}},
    )


def _state(year: int = 2400, *, fleet_x: float = 0.0, in_motion: bool = False) -> GameState:
    target = Planet(1, "Target", Position(10, 0), observed=True)
    fleet = Fleet(
        0,
        "Scout",
        1,
        Position(fleet_x, 0),
        destination_planet_id=1 if in_motion else None,
        role="scout",
        destination_warp=7 if in_motion else None,
        native={
            "ship_count": [1] + [0] * 15,
            "native_destination_warp": 7 if in_motion else None,
            "native_destination_task": 0 if in_motion else None,
        },
    )
    return GameState(
        "STORE", year, 1, RaceProfile(), Tech(),
        [Planet(0, "Home", Position(0, 0), owner=1), target], [fleet],
        native={"header": {"game_id": 77}},
    )


def test_state_store_loads_legacy_memory_and_preserves_every_current_field(tmp_path):
    path = tmp_path / "player-01-memory.json"
    memory = _memory()
    memory.save(path)

    store = AIStateStore(path)
    loaded = store.load()

    assert asdict(loaded.to_agent_memory()) == asdict(memory)
    assert store.pending_path == tmp_path / "player-01-memory.pending.json"


def test_state_store_migrates_old_memory_and_saves_current_schema(tmp_path):
    path = tmp_path / "memory.json"
    path.write_text(json.dumps({
        "schema_version": 4,
        "game_id": 77,
        "player_id": 1,
        "strategic_notes": ["legacy note"],
        "research_state": {"capability_id": "electronics_2"},
        "future_writer_metadata": {"writer": "newer-build"},
    }), encoding="utf-8")

    store = AIStateStore(path)
    loaded = store.load()
    store.stage(loaded)
    store.commit()

    restored = AgentMemory.load(path)
    assert restored.schema_version == AgentMemory().schema_version
    assert restored.strategic_notes == ["legacy note"]
    assert restored.research_state["capability_id"] == "electronics_2"
    assert json.loads(path.read_text(encoding="utf-8"))["future_writer_metadata"] == {
        "writer": "newer-build"
    }


def test_state_store_stage_then_commit_promotes_only_pending_snapshot(tmp_path):
    path = tmp_path / "memory.json"
    original = _memory()
    original.save(path)
    store = AIStateStore(path)

    staged = store.load()
    staged.history.strategic_notes.append("new committed note")
    store.stage(staged)

    assert store.has_staged_state
    assert AgentMemory.load(path).strategic_notes == ["old note"]
    assert store.pending_path is not None and store.pending_path.exists()

    store.commit()

    assert not store.has_staged_state
    assert AgentMemory.load(path).strategic_notes == ["old note", "new committed note"]
    assert store.pending_path is not None and not store.pending_path.exists()


def test_state_store_adopts_writer_pending_snapshot_for_later_host_commit(tmp_path):
    path = tmp_path / "memory.json"
    original = _memory()
    original.save(path)
    pending = tmp_path / "memory.pending.json"
    staged_memory = _memory()
    staged_memory.strategic_notes.append("writer-staged")
    staged_memory.save(pending)
    store = AIStateStore(path, pending)

    store.adopt_pending_file()
    assert store.has_staged_state
    assert AgentMemory.load(path).strategic_notes == ["old note"]

    store.commit()
    assert AgentMemory.load(path).strategic_notes[-1] == "writer-staged"


def test_state_store_rollback_matches_host_failure_semantics(tmp_path):
    path = tmp_path / "memory.json"
    original = _memory()
    original.save(path)
    original_bytes = path.read_bytes()
    store = AIStateStore(path)

    staged = store.load()
    staged.history.strategic_notes.append("must not survive failed host")
    store.stage(staged)
    store.rollback()

    assert path.read_bytes() == original_bytes
    assert store.pending_path is not None and not store.pending_path.exists()
    assert not store.has_staged_state


def test_memory_services_keep_existing_intelligence_action_route_kpi_and_movement_behavior():
    memory = AgentMemory()
    state = _state()

    intelligence = intelligence_memory_service.reconcile(memory, state)
    assert intelligence["ever_observed"] == 2
    assert memory.planet_intel["1"]["last_seen_year"] == 2400

    exploration_memory_service.set_scout_route(memory, 0, [1], state.year)
    assert memory.scout_route(0)["planet_ids"] == [1]
    kpi_history_service.append(memory, {"year": 2400, "owned_planets": 1})
    assert memory.kpi_history[-1]["owned_planets"] == 1

    action_outcome_service.record_emitted(memory, [{
        "kind": "move_fleet",
        "payload": {"fleet_id": 0, "destination_planet_id": 1, "warp": 8, "mission": "move"},
    }], state)
    outcomes = action_outcome_service.evaluate(memory, _state(2401, fleet_x=10))
    assert outcomes[0]["status"] == "COMPLETED"

    first = movement_audit_service.update(memory, _state(2402, fleet_x=0, in_motion=True))
    second = movement_audit_service.update(memory, _state(2403, fleet_x=4, in_motion=True))
    assert first[0]["actual_progress"] is None
    assert second[0]["actual_progress"] == 4.0
