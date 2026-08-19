
from stars_ai.native_observer import ObserverPlayer, ObserverTurn, derive_turn_events, build_human_report

def P(pid,planets=1,pop=100000,factories=10,ships=5,mass=500,tech=10,prt="JOAT"):
    return ObserverPlayer(pid,f"P{pid}",prt,planets,pop,factories,10,0,2,ships,mass,tech,1,0)

def test_capture_is_detected():
    a=ObserverTurn(1,2401,[P(1),P(2)],{"1":1},{})
    b=ObserverTurn(2,2402,[P(1,2),P(2,0)],{"1":2},{})
    ev=derive_turn_events(a,b)
    assert any(e["type"]=="capture" for e in ev)

def test_report_answers_fighting_and_leader():
    a=ObserverTurn(1,2401,[P(1),P(2)],{"1":2},{})
    b=ObserverTurn(2,2402,[P(1,2,200000,30,10,1500,15),P(2,0,50000,5,1,100,8)],{"1":1},{})
    b.events=derive_turn_events(a,b)
    r=build_human_report(b,[a,b],checkpoint_from=a)
    assert "EXECUTIVE SUMMARY" in r
    assert "fighting/conquest has begun" in r
    assert "CURRENT STANDINGS" in r
    assert "Current leader" in r
