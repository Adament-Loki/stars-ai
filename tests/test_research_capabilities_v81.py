from stars_ai.memory import AgentMemory
from stars_ai.models import Fleet, GameState, Planet, Position, RaceProfile, Tech, OrderSet
from stars_ai.native.orders import parse_research_change
from stars_ai.native.x_writer import _planet_leftover_research_block, _research_change_block
from stars_ai.persona import BalancedPersona
from stars_ai.research_planner import plan_research
from stars_ai.strategy.economy import add_economic_orders


def _state(*, year=2405, tech=None, hostile=False, external=None):
    home = Planet(
        0,"Home",Position(0,0),owner=1,population=250000,factories=100,mines=50,
        native={"starbase_capabilities":{"can_build_ships":True,"can_refuel":True}},
    )
    lab = Planet(1,"Lab",Position(20,0),owner=1,population=200000,factories=80,mines=40)
    fleets = [Fleet(0,"Enemy",2,Position(10,0),role="combat",combat_power=200)] if hostile else []
    native = {
        "design_profiles":[{
            "design_number":2,"name":"Medium Freighter","role":"freighter",
            "cargo_capacity":450,"is_starbase":False,
        }],
        "production_by_planet":{
            "0":[{"item_type":4,"item_id":2,"count":1}],
            "1":[{"item_type":2,"item_id":1,"count":8}],
        },
        "strategic_watchdog":{
            "colonization_pressure":1.75,
            "exploration_pressure":1.45,
            "colonization_below_minimum":True,
            "exploration_below_minimum":True,
        },
    }
    if external is not None:
        native["research_demands"] = external
    return GameState(
        "research",year,1,RaceProfile(),tech or Tech(construction=3),
        [home,lab],fleets,native=native,
    )


def _plan(state):
    return BalancedPersona().build_plan(state)


def test_large_freighter_is_named_five_turn_expansion_sprint():
    decision = plan_research(_state(), _plan(_state()))
    assert decision.capability_id == "hull:2"
    assert decision.current_field == "construction"
    assert decision.estimated_turns == 5
    assert decision.posture == "SPRINT"
    assert decision.allocation_percent == 25
    assert decision.contributor_planet_ids == (1,)
    assert 0 in decision.protected_production_planet_ids


def test_sprint_clears_only_noncritical_contributor_and_protects_shipyard():
    state = _state()
    decision = plan_research(state,_plan(state))
    orders=OrderSet(state.game_name,state.year,state.player_id)
    add_economic_orders(state,orders,_plan(state),research_decision=decision)
    lab=next(o for o in orders.orders if o.kind=="set_planet_queue" and o.payload["planet_id"]==1)
    assert lab.payload["queue"] == []
    assert lab.payload["clear_queue"] is True
    assert "Large Freighter" in lab.reason
    home=next(o for o in orders.orders if o.kind=="set_planet_queue" and o.payload["planet_id"]==0)
    assert any(q.get("item")=="ship_design" for q in home.payload["queue"])
    mode=next(o for o in orders.orders if o.kind=="set_planet_research_mode")
    assert mode.payload == {"planet_id":0,"leftover_only":True,"capability_id":"hull:2"}


def test_ife_does_not_research_propulsion_after_fuel_mizer():
    state=_state()
    state.race.native={"lrts":["IFE"]}
    state.tech.propulsion=2
    decision=plan_research(state,_plan(state))
    assert decision.capability_id != "component:fuel_mizer"
    assert decision.current_field != "propulsion"


def test_expansion_debt_suppresses_high_scoring_vanity_demand():
    vanity=[{
        "capability_id":"vanity:electronics","name":"Vanity Electronics","category":"mature",
        "requirements":{"electronics":1},"need":3,"urgency":3,"value":3,"utilization":1,
    }]
    state=_state(external=vanity)
    decision=plan_research(state,_plan(state))
    assert decision.capability_id == "hull:2"


def test_military_emergency_overrides_expansion_sprint():
    state=_state(hostile=True)
    decision=plan_research(state,_plan(state))
    assert decision.category == "military"
    assert decision.posture == "MILITARY_EMERGENCY"
    assert decision.current_field == "weapons"
    assert decision.allocation_percent == 25


def _demand(capability_id,score_scale,field="electronics",level=2,utilization=1.0):
    return {
        "capability_id":capability_id,"name":capability_id,"category":"strategic",
        "requirements":{field:level},"need":score_scale,"urgency":score_scale,
        "value":score_scale,"utilization":utilization,
    }


def test_hysteresis_retains_incumbent_until_challenger_is_materially_better():
    state=_state(external=[_demand("incumbent",1.4),_demand("challenger",1.5,"biotechnology")])
    state.native["strategic_watchdog"]={}
    memory=AgentMemory(research_state={"capability_id":"incumbent"})
    decision=plan_research(state,_plan(state),memory)
    assert decision.capability_id == "incumbent"
    assert "less than 25% stronger" in decision.reason


def test_material_challenger_can_replace_incumbent():
    state=_state(external=[_demand("incumbent",1.0),_demand("challenger",2.5,"biotechnology")])
    state.native["strategic_watchdog"]={}
    memory=AgentMemory(research_state={"capability_id":"incumbent"})
    assert plan_research(state,_plan(state),memory).capability_id == "challenger"


def test_stalled_sprint_enters_recovery_instead_of_continuing_25_percent():
    demand=_demand("stalled",2.0,level=4)
    state=_state(year=2410,external=[demand])
    state.native["strategic_watchdog"]={}
    memory=AgentMemory(research_state={
        "capability_id":"stalled","capability_name":"stalled","posture":"SPRINT",
        "selected_year":2400,"estimated_turns":1,"remaining_total":1,
    })
    decision=plan_research(state,_plan(state),memory)
    assert decision.posture == "RECOVERY"
    assert decision.allocation_percent == 15
    assert decision.sprint_stalled is True


def test_low_utilization_capability_is_not_sprinted():
    state=_state(external=[_demand("blocked",4.0,utilization=0.2)])
    state.native["strategic_watchdog"]={}
    decision=plan_research(state,_plan(state))
    assert decision.capability_id == "blocked"
    assert decision.allocation_percent == 15


def test_unlock_is_recorded_and_next_named_capability_selected():
    state=_state(tech=Tech(construction=8))
    memory=AgentMemory(research_state={
        "capability_id":"hull:2","capability_name":"Large Freighter",
        "requirements":{"construction":8},
    })
    decision=plan_research(state,_plan(state),memory)
    assert "Large Freighter" in decision.recently_unlocked
    assert decision.capability_id != "hull:2"


def test_native_research_nibbles_and_leftover_only_bytes():
    block=_research_change_block("construction",25,"electronics")
    assert block.data==bytes.fromhex("19 43")
    parsed=parse_research_change(block.data)
    assert parsed.percent==25
    assert parsed.current_field_code==3
    assert parsed.next_field_code==4
    assert _planet_leftover_research_block(40).data==bytes.fromhex("28 00 01 00 00 00")


def test_research_and_planet_mode_commands_are_status_aware():
    state=_state()
    memory=AgentMemory()
    memory.record_emitted_actions([
        {"kind":"set_research","payload":{
            "current_field":"construction","next_field":"electronics",
            "allocation_percent":25,"capability_id":"hull:2",
        }},
        {"kind":"set_planet_research_mode","payload":{"planet_id":0,"leftover_only":True}},
    ],state)
    state.year+=1
    state.native.update({
        "current_research_field":"construction",
        "next_research_field":"electronics",
        "research_allocation_percent":25,
        "planet_research_modes":{"0":1},
    })
    outcomes=memory.evaluate_action_outcomes(state)
    assert [x["status"] for x in outcomes] == ["COMPLETED","COMPLETED"]
