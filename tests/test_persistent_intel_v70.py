
from stars_ai.memory import AgentMemory
from stars_ai.models import GameState,RaceProfile,Tech,Planet,Fleet,Position,OrderSet
from stars_ai.strategy.exploration import add_exploration_orders
from stars_ai.strategic_watchdog import evaluate_strategic_watchdog


def _state(year=2400, observed_ids=(), owners=None):
    owners=owners or {}
    planets=[
        Planet(
            i,
            f"P{i}",
            Position(float(i*10),0),
            owner=owners.get(i),
            habitability=(70 if i in observed_ids else None),
            observed=(i in observed_ids),
            native=(
                {
                    "environment":[50,50,50],
                    "mineral_concentrations":[40+i,50+i,60+i],
                    "observed_turn":year-2400,
                }
                if i in observed_ids else
                {"map_only":True,"observed_turn":None}
            ),
        )
        for i in range(8)
    ]
    return GameState(
        "g",year,1,RaceProfile(),Tech(),planets,[],
        native={"header":{"game_id":1234,"year":year}},
    )


def test_ever_observed_planet_survives_sparse_next_m_file():
    mem=AgentMemory()
    s0=_state(2400,observed_ids={0,1},owners={0:1})
    d0=mem.reconcile_state(s0)
    assert d0["ever_observed"]==2

    # Next M only exposes P0. P1 must remain known strategically.
    s1=_state(2401,observed_ids={0},owners={0:1})
    d1=mem.reconcile_state(s1)

    p1=next(p for p in s1.planets if p.id==1)
    assert p1.observed is True
    assert p1.habitability==70
    assert p1.native["intel_source"]=="persistent_memory"
    assert p1.native["mineral_concentrations"]==[41,51,61]
    assert d1["ever_observed"]==2
    assert d1["restored_from_memory"]==1


def test_current_m_unowned_record_revokes_stale_local_ownership():
    """A lost world remains known, but cannot return as a local economy target."""
    mem=AgentMemory()
    s0=_state(2478,observed_ids={0,1},owners={0:1,1:1})
    for planet in s0.planets:
        planet.native.update({
            "current_m_record":True,
            "current_m_owner":planet.owner,
            "current_m_owned_by_player":planet.owner==1,
        })
    mem.reconcile_state(s0)

    # The next M file still names P1, but its current record explicitly says
    # the planet is unowned after a capture/abandonment event.
    s1=_state(2479,observed_ids={0},owners={0:1})
    lost=next(p for p in s1.planets if p.id==1)
    lost.native={
        "current_m_record":True,
        "current_m_owner":None,
        "current_m_owned_by_player":False,
    }
    diag=mem.reconcile_state(s1)

    assert lost.observed is True  # durable exploration knowledge remains
    assert lost.owner is None
    assert lost.native["intel_source"]=="current_m_unowned"
    assert lost.native["native_planet_mutation_allowed"] is False
    assert mem.planet_intel["1"]["last_known_owner"] is None
    assert diag["current_m_ownership_revoked_planet_ids"]==[1]


def test_known_world_count_is_monotonic_across_sparse_turns():
    mem=AgentMemory()
    counts=[]
    for year,seen in [
        (2400,{0}),
        (2401,{0,1,2}),
        (2402,{0}),
        (2403,{0,3}),
        (2404,set()),
    ]:
        s=_state(year,observed_ids=seen,owners={0:1})
        mem.reconcile_state(s)
        counts.append(mem.ever_observed_count())

    assert counts==sorted(counts)
    assert counts==[1,3,3,4,4]


def test_memory_resets_when_same_game_is_rewound_for_new_playtest():
    mem=AgentMemory()
    mem.reconcile_state(_state(2475,observed_ids={0,1,2,3},owners={0:1}))
    assert mem.ever_observed_count()==4

    diag=mem.reconcile_state(_state(2400,observed_ids={0},owners={0:1}))
    assert diag["memory_reset"] is True
    assert mem.ever_observed_count()==1
    assert mem.start_year==2400


def test_new_colonies_are_counted_relative_to_opening_empire():
    mem=AgentMemory()
    mem.reconcile_state(_state(2400,observed_ids={0,1},owners={0:1,1:1}))
    assert mem.new_colonies_count()==0

    mem.reconcile_state(_state(2405,observed_ids={0,1,2},owners={0:1,1:1,2:1}))
    assert mem.new_colonies_count()==1
    assert mem.colonized_years["2"]==2405


def test_recent_scan_assignment_is_not_selected_again_next_turn():
    mem=AgentMemory()
    s=_state(2400,observed_ids={0},owners={0:1})
    s.fleets=[Fleet(0,"Scout",1,Position(0,0),role="scout")]
    mem.reconcile_state(s)

    # Pretend last turn assigned P1 but it did not yet become observed.
    o=OrderSet("g",2400,1)
    o.add(
        "move_fleet",
        {"fleet_id":0,"destination_planet_id":1,"warp":6,"mission":"scan"},
        "test",
    )
    mem.record_scan_orders(o,2400)

    s1=_state(2401,observed_ids={0},owners={0:1})
    s1.fleets=[Fleet(0,"Scout",1,Position(0,0),role="scout")]
    mem.reconcile_state(s1)
    s1.native["recent_scan_targets"]=sorted(mem.recent_scan_target_ids(2401,3))
    s1.native["strategic_watchdog"]={"exploration_pressure":1.0}

    out=OrderSet("g",2401,1)
    add_exploration_orders(s1,out,None)
    scan=next(x for x in out.orders if x.kind=="move_fleet")
    assert scan.payload["destination_planet_id"] != 1


def test_turn10_watchdog_uses_hard_numbers_requested_for_opening():
    mem=AgentMemory()
    s=_state(2410,observed_ids={0,1,2,3,4,5,6,7},owners={0:1})
    mem.reconcile_state(s)
    status=evaluate_strategic_watchdog(s,mem)
    m=status["milestone"]

    assert m["mode"]=="hard_numbers"
    assert m["deadline_turn"]==10
    assert m["explored_min"]==10
    assert m["explored_optimal"]==25
    assert m["new_colonies_min"]==4


def test_after_turn25_exploration_goal_becomes_percentage_based():
    # 200-world map makes the percent conversion unambiguous.
    planets=[
        Planet(i,f"P{i}",Position(i,0),owner=(1 if i==0 else None),
               observed=(i<40),habitability=(70 if i<40 else None))
        for i in range(200)
    ]
    s=GameState(
        "g",2430,1,RaceProfile(),Tech(),planets,[],
        native={"header":{"game_id":999}},
    )
    mem=AgentMemory()
    mem.reconcile_state(s)
    status=evaluate_strategic_watchdog(s,mem)
    m=status["milestone"]

    assert m["mode"]=="coverage_percentage"
    assert m["deadline_turn"]==40
    assert m["explored_min_percent"]==45.0
    assert m["explored_min"]==90
    # Colony targets deliberately remain absolute counts.
    assert m["new_colonies_min"]==12
