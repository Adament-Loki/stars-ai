
from stars_ai.native_observer import (
    ObserverPlayer, ObserverTurn, derive_turn_events, build_human_report,
    build_running_game_report,
)
from stars_ai.windows_autohost import _write_running_observer_report

def P(pid,planets=1,pop=100000,factories=10,ships=5,mass=500,tech=10,prt="JOAT",*,fleets=2):
    return ObserverPlayer(pid,f"P{pid}",prt,planets,pop,factories,10,0,fleets,ships,mass,tech,1,0)

def test_capture_is_detected():
    a=ObserverTurn(1,2401,[P(1),P(2)],{"1":1},{})
    b=ObserverTurn(2,2402,[P(1,2),P(2,0)],{"1":2},{})
    ev=derive_turn_events(a,b)
    assert any(e["type"]=="capture" for e in ev)


def test_major_fleet_loss_identifies_turn_and_disappeared_fleet():
    previous=ObserverTurn(
        14,2414,[P(1,fleets=11,ships=16,mass=0)],{"1":1},
        {"1:9":{"owner":1,"fleet_id":9,"ships":4,"mass":0,"x":1679,"y":1809}},
    )
    current=ObserverTurn(15,2415,[P(1,fleets=10,ships=12,mass=0)],{"1":1},{})

    loss=derive_turn_events(previous,current)[0]

    assert loss["type"] == "major_fleet_loss"
    assert loss["turn"] == 15
    assert loss["year"] == 2415
    assert loss["ships_before"] == 16
    assert loss["ships_after"] == 12
    assert loss["fleets_before"] == 11
    assert loss["fleets_after"] == 10
    assert loss["affected_fleets"] == [{
        "fleet_id":9,"status":"disappeared","ships_before":4,"ships_lost":4,
        "mass_before":0,"mass_lost":0,"last_x":1679,"last_y":1809,
    }]
    assert "Turn 15 / Year 2415" in loss["text"]
    assert "fleet ID 9 disappeared (4 ships; last seen at (1679, 1809))" in loss["text"]
    assert "Cause is not decoded." in loss["text"]

def test_report_answers_fighting_and_leader():
    a=ObserverTurn(1,2401,[P(1),P(2)],{"1":2},{})
    b=ObserverTurn(2,2402,[P(1,2,200000,30,10,1500,15),P(2,0,50000,5,1,100,8)],{"1":1},{})
    b.events=derive_turn_events(a,b)
    r=build_human_report(b,[a,b],checkpoint_from=a)
    assert "EXECUTIVE SUMMARY" in r
    assert "fighting/conquest has begun" in r
    assert "CURRENT STANDINGS" in r
    assert "Current leader" in r


def test_running_report_has_each_turn_status_and_configured_major_report():
    baseline=ObserverTurn(0,2400,[P(1),P(2)],{"1":1,"2":2},{})
    turn_one=ObserverTurn(1,2401,[P(1,2,125000,15,7,800,11),P(2,1,90000,11,4,400,10)],{"1":1,"2":1},{})
    turn_one.events=derive_turn_events(baseline,turn_one)
    turn_two=ObserverTurn(2,2402,[P(1,2,140000,22,2,200,12),P(2,0,70000,8,2,200,10)],{"1":1},{})
    turn_two.events=derive_turn_events(turn_one,turn_two)

    report=build_running_game_report(
        [baseline,turn_one,turn_two],
        personas={"1":"Balanced"},major_report_turns=[2],
    )

    assert "## Turn 001 - Year 2401" in report
    assert "## Turn 002 - Year 2402" in report
    assert "P1 P1 (JOAT)" in report
    assert "AI/Balanced" in report
    assert "external/human" in report
    assert "Major report - Turn 002" in report
    assert "fighting/conquest has begun" in report


def test_running_report_is_refreshed_at_the_stable_run_root(tmp_path):
    baseline=ObserverTurn(0,2400,[P(1)],{"1":1},{})
    turn_one=ObserverTurn(1,2401,[P(1,2,125000,15,7,800,11)],{"1":1},{})
    path=_write_running_observer_report(
        tmp_path,[baseline,turn_one],personas={"1":"Balanced"},
        major_report_turns=[],
    )

    assert path == tmp_path/"RUNNING_GAME_REPORT.md"
    assert "## Turn 001 - Year 2401" in path.read_text(encoding="utf-8")
