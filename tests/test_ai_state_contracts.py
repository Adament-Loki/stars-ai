"""Phase 2 contracts for typed AI state and legacy-memory compatibility."""

from __future__ import annotations

from dataclasses import asdict
import json

from stars_ai.ai.ai_state import AIState, PersistentAIState, TurnAIState
from stars_ai.memory import AgentMemory
from stars_ai.models import GameState, RaceProfile, Tech


def _memory_with_every_persistent_field() -> AgentMemory:
    return AgentMemory(
        schema_version=8,
        game_id=77,
        player_id=4,
        start_year=2400,
        last_year=2412,
        known_enemy_planets={"8": 2},
        planet_scores={"8": 67.5},
        strategic_notes=["expand safely"],
        goal_progress={"expand": 0.5},
        diplomacy={"2": {"attitude": "hostile"}},
        initial_owned_planet_ids=[1, 2],
        planet_intel={"8": {"name": "Triton", "ever_observed": True}},
        colonized_years={"2": 2408},
        scan_target_history={"8": {"last_year": 2412}},
        scout_routes={"9": {"destination_planet_id": 8}},
        kpi_history=[{"year": 2412, "owned_planets": 4}],
        fleet_movement_history={"9": {"range": 12.0}},
        action_expectations={"move:9": {"due_year": 2413}},
        action_outcome_history=[{"action_id": "move:9", "status": "PENDING"}],
        research_state={"capability_id": "propulsion_3"},
        research_history=[{"year": 2412, "posture": "SPRINT"}],
        territorial_exposure_history={"pressure_reserve": 1.5},
        capability_execution={"design:7": {"status": "HOST_CONFIRMED"}},
    )


def test_persistent_state_round_trips_every_existing_memory_field():
    memory = _memory_with_every_persistent_field()

    persistent = PersistentAIState.from_agent_memory(memory)
    restored = persistent.to_agent_memory()

    assert asdict(restored) == asdict(memory)
    assert persistent.identity.player_id == 4
    assert persistent.intelligence.planet_intel["8"]["name"] == "Triton"
    assert persistent.exploration.scout_routes["9"]["destination_planet_id"] == 8
    assert persistent.action_ledger.capability_execution["design:7"]["status"] == "HOST_CONFIRMED"

    memory.planet_intel["8"]["name"] = "changed after conversion"
    assert persistent.intelligence.planet_intel["8"]["name"] == "Triton"


def test_existing_memory_json_loads_and_remains_json_compatible(tmp_path):
    old_path = tmp_path / "memory-v4.json"
    old_path.write_text(json.dumps({
        "schema_version": 4,
        "game_id": 91,
        "player_id": 3,
        "known_enemy_planets": {"10": 2},
        "strategic_notes": ["legacy note"],
        "research_state": {"capability_id": "electronics_2"},
        "unknown_future_field": "ignored by existing loader",
    }), encoding="utf-8")

    loaded = AgentMemory.load(old_path)
    persistent = PersistentAIState.from_agent_memory(loaded)
    restored = persistent.to_agent_memory()
    round_trip_path = tmp_path / "memory-round-trip.json"
    restored.save(round_trip_path)

    assert asdict(AgentMemory.load(round_trip_path)) == asdict(loaded)
    assert persistent.identity.schema_version == AgentMemory().schema_version
    assert persistent.intelligence.known_enemy_planets == {"10": 2}
    assert persistent.research.state["capability_id"] == "electronics_2"


def test_ai_state_keeps_observed_persistent_and_turn_lifetimes_separate():
    game = GameState("STATE", 2410, 1, RaceProfile(), Tech(), [], [])
    memory = _memory_with_every_persistent_field()
    state = AIState.from_agent_memory(game, memory)

    assert state.game is game
    assert state.persistent.identity.game_id == 77
    assert state.turn.strategic_phase is None

    state.turn.warnings.append("turn-only warning")
    assert memory.strategic_notes == ["expand safely"]
    assert game.native == {}


def test_turn_state_copies_known_legacy_runtime_scratch_without_sharing_it():
    native = {
        "strategic_phase": {"phase": "EXPAND", "reason": "claim window", "assessment": {"risk": 1}},
        "strategic_watchdog": {
            "year": 2410,
            "turn": 10,
            "total_planets": 50,
            "explored_count": 20,
            "explored_percent": 40.0,
            "new_colonies": 4,
            "owned_planets": 6,
            "exploration_pressure": 1.45,
            "colonization_pressure": 1.2,
            "milestone": {"deadline_turn": 15},
            "notes": ["watchdog note"],
            "competitive_position": {"status": "TRAILING"},
        },
        "research_strategy": {
            "capability_id": "propulsion_3",
            "capability_name": "Propulsion 3",
            "category": "expansion",
            "posture": "SPRINT",
            "current_field": "propulsion",
            "next_field": "propulsion",
            "allocation_percent": 100,
            "estimated_turns": 1,
            "reason": "fleet range",
            "commitment": "SPRINT",
            "execution_status": "ACTIVE",
            "expected_resources": 300,
            "requirements": {"propulsion": 3},
            "remaining_requirements": {"propulsion": 1},
            "contributor_planet_ids": [1],
            "protected_production_planet_ids": [2],
        },
        "territorial_defense": {
            "violations": [{"fleet_id": 8}],
            "uncovered_minefield_anchor_ids": [2],
            "desired_patrols": 2,
            "desired_minelayers": 1,
            "reason": "border contact",
            "exposure": {"sustained_pressure": 2.0},
            "source_escalation": {"status": "WATCH"},
        },
        "command_outcomes": [{
            "status": "WARNING", "message": "fleet delayed", "action_id": "move:8", "due_year": 2409,
        }],
        "movement_progress_diagnostics": [{
            "fleet_id": 8, "destination_planet_id": 3, "actual_progress": 0.0, "flag": "STALLED",
        }],
        "capability_execution_diagnostics": {"blocked_capability_ids": ["design:7"]},
        "waypoint_diagnostics": [{"fleet_id": 8, "status": "SAFE"}],
    }
    fleet_intents = [{"fleet_id": 8, "status": "BLOCKED", "reason": "fuel"}]

    turn = TurnAIState.from_legacy_native(native, fleet_intents=fleet_intents)

    assert turn.strategic_phase is not None and turn.strategic_phase.phase == "EXPAND"
    assert turn.strategic_watchdog is not None and turn.strategic_watchdog.exploration_pressure == 1.45
    assert turn.research_decision is not None and turn.research_decision.expected_resources == 300
    assert turn.territorial_defense is not None and turn.territorial_defense.needs_response
    assert turn.command_outcomes[0].action_id == "move:8"
    assert turn.warnings == ["fleet delayed"]
    assert turn.movement_progress_diagnostics[0].flag == "STALLED"
    assert turn.blocked_actions[0].action_id == "design:7"
    assert turn.blocked_actions[0].status == "BLOCKED_NATIVE"
    assert turn.safety_diagnostics[0].source == "waypoint"
    assert turn.safety_diagnostics[0].fleet_id == 8
    assert turn.fleet_intents[0].reason == "fuel"

    native["strategic_watchdog"]["milestone"]["deadline_turn"] = 99
    native["command_outcomes"][0]["message"] = "mutated"
    fleet_intents[0]["reason"] = "mutated"
    assert turn.strategic_watchdog.milestone["deadline_turn"] == 15
    assert turn.command_outcomes[0].message == "fleet delayed"
    assert turn.fleet_intents[0].reason == "fuel"
