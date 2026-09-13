
from stars_ai.memory import AgentMemory
from stars_ai.models import GameState,RaceProfile,Tech,Planet,Fleet,Position,OrderSet
from stars_ai.strategy.exploration import add_exploration_orders
from stars_ai.strategy.economy import add_economic_orders
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


def test_lost_world_purges_local_actions_but_retains_enemy_strategic_intel():
    mem=AgentMemory()
    s0=_state(2478,observed_ids={0,1},owners={0:1,1:1})
    for planet in s0.planets:
        planet.native.update({
            "current_m_record":True,
            "current_m_owner":planet.owner,
            "current_m_owned_by_player":planet.owner==1,
        })
    prior_lost=next(planet for planet in s0.planets if planet.id==1)
    prior_lost.population=28_000
    prior_lost.factories=90
    prior_lost.mines=50
    prior_lost.ironium=410
    prior_lost.boranium=320
    prior_lost.germanium=230
    mem.reconcile_state(s0)
    # Preserve historical colonization timing for rate analysis; its live
    # expansion contribution must disappear once the world is captured.
    mem.colonized_years["1"]=2478
    mem.fleet_movement_history["9"]={
        "fleet_id":9,"destination_planet_id":1,"year":2478,
    }
    mem.action_expectations={
        "queue-lost":{
            "id":"queue-lost","kind":"production_queue","planet_id":1,
            "planet_name":"P1","description":"P1 production queue",
        },
        "export-lost-source":{
            "id":"export-lost-source","kind":"transport","planet_id":0,
            "source_planet_id":1,"population_export_hub_id":1,
            "description":"freighter export from P1",
        },
        "action-lost-target":{
            "id":"action-lost-target","kind":"fleet_arrival","planet_id":1,
            "description":"freighter arrive at P1",
        },
        "unrelated":{
            "id":"unrelated","kind":"production_queue","planet_id":0,
            "description":"P0 production queue",
        },
    }

    # P1 is now visibly captured by Player 2. Its reported population/mineral
    # data remains usable tactical intelligence, never local economy state.
    s1=_state(2479,observed_ids={0,1},owners={0:1,1:2})
    captured=next(planet for planet in s1.planets if planet.id==1)
    # The enemy ownership is visible, but its detailed economic record is not.
    # Retain the last friendly observation as stale tactical intelligence.
    captured.population=0
    captured.ironium=0
    captured.native.update({
        "current_m_record":True,
        "current_m_owner":2,
        "current_m_owned_by_player":False,
    })
    home=next(planet for planet in s1.planets if planet.id==0)
    home.native.update({
        "current_m_record":True,
        "current_m_owner":1,
        "current_m_owned_by_player":True,
    })
    s1.native.update({
        "production_by_planet":{"0":[{"item_name":"Factory"}],"1":[{"item_name":"Mine"}]},
        "planet_research_modes":{"0":1,"1":1},
    })

    diag=mem.reconcile_state(s1)

    assert diag["lost_owned_planet_ids"]==[1]
    assert set(diag["purged_pending_action_ids"]) == {
        "queue-lost","export-lost-source","action-lost-target",
    }
    assert set(mem.action_expectations)=={"unrelated"}
    assert "9" not in mem.fleet_movement_history
    assert s1.native["production_by_planet"]=={"0":[{"item_name":"Factory"}]}
    assert s1.native["planet_research_modes"]=={"0":1}
    assert mem.planet_intel["1"]["last_known_owner"]==2
    assert mem.planet_intel["1"]["population"]==28_000
    assert mem.planet_intel["1"]["ironium"]==410
    assert mem.planet_intel["1"]["strategic_stats_source"]=="last_friendly_observation"
    assert mem.known_enemy_planets["1"]==2
    assert captured.native["recently_lost_by_player"] is True
    assert captured.native["tactical_population_estimate"]==28_000
    assert captured.native["tactical_population_estimate_source"]=="last_friendly_observation"
    assert mem.colonized_years["1"]==2478
    assert mem.new_colonies_count()==0
    assert {
        outcome["id"] for outcome in mem.action_outcome_history
        if outcome.get("status")=="CANCELLED_WORLD_LOST"
    } == {"queue-lost","export-lost-source","action-lost-target"}


def test_economy_does_not_propose_a_queue_for_memory_only_owned_world():
    state=_state(2410,observed_ids={0},owners={0:1,1:1})
    state.native["current_m_owned_planet_ids"]=[0]
    # P1 exists only as retained memory in this synthetic current turn.
    stale=next(planet for planet in state.planets if planet.id==1)
    stale.native={"current_m_record":False,"current_m_owner":None}

    orders=OrderSet("g",state.year,1)
    add_economic_orders(state,orders)

    queue_ids={int(order.payload["planet_id"]) for order in orders.orders if order.kind=="set_planet_queue"}
    assert queue_ids == {0}


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


def test_unobserved_home_neighborhood_scan_is_retried_next_turn():
    mem=AgentMemory()
    s=_state(2400,observed_ids={0},owners={0:1})
    s.fleets=[Fleet(0,"Scout",1,Position(0,0),role="scout")]
    mem.reconcile_state(s)

    # Pretend last turn assigned P1 but it did not yet become observed.  A
    # close target remains a priority until the M file confirms its scan; a
    # stale memory reservation must not leave the home ring unexplored.
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
    assert scan.payload["destination_planet_id"] == 1


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
