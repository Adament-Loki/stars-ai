from stars_ai.models import Fleet, GameState, Planet, Position, RaceProfile, Tech, OrderSet
from stars_ai.population_redistribution import (
    plan_population_redistribution,
    plan_empty_freighter_returns,
    add_population_redistribution_orders,
)
from stars_ai.logistics_capacity import HOMEWORLD_EARLY_EXPORT_COLONISTS, HOMEWORLD_EARLY_EXPORT_KT


def _fleet(fid, x=0, planet_id=0):
    return Fleet(fid,f"Transport {fid}",1,Position(x,0),role="freighter",cargo_capacity=250,native={
        "position_object_id":planet_id,
        "cargo_capacity":250,
        "cargo":{"ironium":0,"boranium":0,"germanium":0,"population":0},
    })


def _state(home_pop=100_000, two_freighters=False):
    race=RaceProfile(growth_rate=.15,native={"lrts":["IFE"]})
    home=Planet(0,"Home",Position(0,0),owner=1,habitability=100,population=home_pop,
        factories=40,mines=30,ironium=300,boranium=200,germanium=200,
        native={"is_homeworld":True,"starbase_capabilities":{"can_build_ships":True,"can_refuel":True}})
    child=Planet(1,"Ring One",Position(120,0),owner=1,habitability=80,population=50_000,
        factories=10,mines=10,ironium=20,boranium=20,germanium=20,native={})
    frontier=[Planet(10+i,f"F{i}",Position(150+i*7,20),owner=None,habitability=70,observed=True) for i in range(5)]
    fleets=[_fleet(3)]
    if two_freighters:
        fleets.append(_fleet(4))
    return GameState("pop",2415,1,race,Tech(construction=4,propulsion=2),[home,child,*frontier],fleets,native={"design_profiles":[]})


def test_100k_homeworld_dispatches_one_gentle_8k_pulse_and_keeps_92k():
    state=_state(two_freighters=True)
    intents=plan_population_redistribution(state)
    assert len(intents)==1
    x=intents[0]
    assert x.population_kt==HOMEWORLD_EARLY_EXPORT_KT==80
    assert x.population_colonists==HOMEWORLD_EARLY_EXPORT_COLONISTS==8_000
    assert x.source_population_before==100_000
    assert x.source_population_after==92_000
    assert x.source_protected_floor==80_000
    assert "only population departure" in x.reason


def test_homeworld_below_88k_does_not_dispatch():
    assert plan_population_redistribution(_state(home_pop=87_900))==[]


def test_two_waiting_transports_do_not_double_strip_one_source():
    state=_state(home_pop=140_000,two_freighters=True)
    intents=plan_population_redistribution(state,max_transfers=4)
    assert len(intents)==1
    assert intents[0].population_colonists==8_000


def test_empty_transport_returns_only_when_exporter_has_a_current_loadable_payload():
    state=_state(home_pop=85_000)
    # Move the empty ship to the child world. Home is still replenishing and
    # cannot load a legal pulse this turn, so there is no reason to fly empty.
    f=state.fleets[0]
    f.position=Position(120,0)
    f.native["position_object_id"]=1
    assert plan_empty_freighter_returns(state)==[]

    state.planets[0].population=100_000
    returns=plan_empty_freighter_returns(state)
    assert returns and returns[0].export_planet_id==0
    assert returns[0].current_planet_id==1


def test_layer1_graduation_stops_hw_feed_and_makes_hub_parent_for_layer2():
    state=_state(home_pop=180_000)
    child=next(p for p in state.planets if p.id==1)
    child.population=270_000
    child.native={"starbase_capabilities":{"can_build_ships":True,"can_refuel":True},"has_starbase":True}
    layer2=Planet(2,"Ring Two",Position(270,0),owner=1,habitability=80,population=40_000,
        factories=5,mines=5,ironium=10,boranium=10,germanium=10,native={})
    state.planets.append(layer2)
    # Put a compact transport at graduated L1.
    state.fleets=[_fleet(8,120,1)]
    intents=plan_population_redistribution(state)
    assert intents
    x=intents[0]
    assert x.source_planet_id==1
    assert x.destination_planet_id==2
    assert x.source_ring==1 and x.destination_ring==2


def test_order_payload_never_credits_stargate_for_loaded_population():
    state=_state(); orders=OrderSet("pop",2415,1)
    intents=add_population_redistribution_orders(state,orders)
    assert intents
    order=next(o for o in orders.orders if o.kind=="transport_population")
    assert order.payload["population_kt"]==80
    assert order.payload["gate_allowed_while_loaded"] is False
    assert order.payload["population_dispatch_policy"]=="one_capped_export_per_source_per_turn"
    assert order.payload["native_experiment"]["trust_level"]=="EXPERIMENTAL"
