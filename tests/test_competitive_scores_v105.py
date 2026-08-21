from stars_ai.competitive_position import evaluate_competitive_position
from stars_ai.models import GameState, Planet, Position, RaceProfile, Tech
from stars_ai.native.player_scores import parse_player_score
from stars_ai.strategic_watchdog import evaluate_strategic_watchdog
from stars_ai.memory import AgentMemory


def _state(scores, visibility="public"):
    return GameState(
        "scores", 2450, 1, RaceProfile(), Tech(),
        [Planet(0, "Home", Position(0, 0), owner=1, observed=True)], [],
        native={
            "player_scores": scores,
            "score_visibility": visibility,
        },
    )


def test_type45_score_record_matches_archived_turn_50_player_one_fields():
    # GAME.m1, turn 50: P1 rank 3, score 167, 17 planets, tech total 28.
    record=parse_player_score(bytes.fromhex(
        "20 00 03 00 A7 00 00 00 6F 08 00 00 11 00 02 00 "
        "0A 00 07 00 00 00 1C 00"
    ))

    assert record.player_id == 1
    assert record.rank == 3
    assert record.score == 167
    assert record.planets == 17
    assert record.tech_total == 28


def test_public_score_deficit_raises_expansion_and_exploration_pressure():
    state=_state([
        {"player_id":1,"rank":3,"score":167},
        {"player_id":2,"rank":2,"score":215},
        {"player_id":3,"rank":1,"score":438},
    ])
    position=evaluate_competitive_position(state)
    memory=AgentMemory()
    memory.reconcile_state(state)
    watchdog=evaluate_strategic_watchdog(state,memory)

    assert position["status"] == "SEVERELY_TRAILING"
    assert position["leader_player_id"] == 3
    assert position["catch_up_pressure"] == 2.0
    assert watchdog["exploration_pressure"] >= 2.0
    assert watchdog["colonization_pressure"] >= 2.0


def test_private_score_uses_self_trend_without_exposing_rival_totals():
    state=_state([{"player_id":1,"rank":1,"score":200}], visibility="private")

    position=evaluate_competitive_position(state)

    assert position["visibility"] == "private"
    assert position["our_score"] == 200
    assert position["catch_up_pressure"] == 1.0
    assert "leader_score" not in position
