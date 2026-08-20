
from stars_ai.memory import AgentMemory
from stars_ai.models import GameState,RaceProfile,Tech,Planet,Fleet,Position,OrderSet
from stars_ai.strategy.exploration import add_exploration_orders


def _turn_state(year:int, current_seen:set[int]):
    planets=[]
    for i in range(30):
        observed=i in current_seen
        planets.append(
            Planet(
                i,f"P{i}",Position(i*10.0,0),
                owner=(1 if i==0 else None),
                observed=observed,
                habitability=(70 if observed else None),
                native=(
                    {"environment":[50,50,50],"mineral_concentrations":[50,50,50]}
                    if observed else {"map_only":True}
                ),
            )
        )
    fleets=[Fleet(0,"Scout",1,Position(0,0),role="scout")]
    return GameState(
        "campaign",year,1,RaceProfile(),Tech(),planets,fleets,
        native={"header":{"game_id":777}},
    )


def test_sparse_m_campaign_never_revisits_a_previously_explored_planet():
    mem=AgentMemory()
    current_seen={0}
    all_targets=[]
    last_target_year={}

    for year in range(2400,2410):
        s=_turn_state(year,current_seen)
        mem.reconcile_state(s)
        s.native["recent_scan_targets"]=sorted(mem.recent_scan_target_ids(year,3))
        s.native["strategic_watchdog"]={"exploration_pressure":1.0}

        o=OrderSet("campaign",year,1)
        add_exploration_orders(s,o,None)
        scan=next(
            (x for x in o.orders if x.kind=="move_fleet" and x.payload.get("mission")=="scan"),
            None,
        )
        assert scan is not None
        target=int(scan.payload["destination_planet_id"])
        assert target not in last_target_year
        last_target_year[target]=year
        all_targets.append(target)
        mem.record_scan_orders(o,year)

        # Simulate next M containing only homeworld + the planet reached this turn,
        # not the complete historical observation set.
        current_seen={0,target}

    assert len(all_targets)==10
    assert len(set(all_targets))>=6
    assert mem.ever_observed_count()>=6
