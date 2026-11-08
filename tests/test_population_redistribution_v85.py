from stars_ai.models import Fleet, GameState, Planet, Position, RaceProfile, Tech, OrderSet
from stars_ai.population_redistribution import (
    plan_population_redistribution,
    plan_empty_freighter_returns,
    add_population_redistribution_orders,
)


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


def test_homeworld_below_the_growth_band_does_not_export():
    assert plan_population_redistribution(_state(home_pop=100_000))==[]


def test_homeworld_exports_surplus_above_the_quarter_capacity_hold():
    state=_state(home_pop=320_000, two_freighters=True)
    intents=plan_population_redistribution(state)
    assert len(intents)==1
    x=intents[0]
    assert x.population_kt==250
    assert x.population_colonists==25_000
    assert x.source_population_before==320_000
    assert x.source_population_after==295_000
    assert x.source_protected_floor==250_000
    assert "Source dispatch #1" in x.reason


def test_homeworld_at_protected_floor_does_not_dispatch():
    assert plan_population_redistribution(_state(home_pop=250_000))==[]


def test_homeworld_uses_freighter_capacity_after_reaching_200k_population():
    intent=plan_population_redistribution(_state(home_pop=320_000))[0]
    assert intent.population_kt==250
    assert intent.population_colonists==25_000
    assert intent.source_population_after==295_000
    assert intent.native_experiment["trust_level"]=="EXPERIMENTAL"


def test_two_waiting_transports_do_not_double_strip_one_source():
    state=_state(home_pop=280_000,two_freighters=True)
    intents=plan_population_redistribution(state,max_transfers=4)
    assert len(intents)==1
    assert intents[0].population_colonists==25_000


def test_empty_transport_stages_at_replenishing_exporter_before_next_packet_is_ready():
    state=_state(home_pop=250_000)
    # Move the empty ship to the child world. The homeworld is at its protected
    # floor, but is an active source for this child and will replenish next
    # turn. The hull should wait at its export hub, not hold at the outpost.
    f=state.fleets[0]
    f.position=Position(120,0)
    f.native["position_object_id"]=1
    returns=plan_empty_freighter_returns(state)
    assert returns and returns[0].export_planet_id==0

    state.planets[0].population=320_000
    returns=plan_empty_freighter_returns(state)
    assert returns and returns[0].export_planet_id==0
    assert returns[0].current_planet_id==1


def test_empty_return_stages_at_exporter_after_sibling_claims_current_packet():
    state=_state(home_pop=280_000,two_freighters=True)
    state.fleets[1].position=Position(120,0)
    state.fleets[1].native["position_object_id"]=1

    transfers=plan_population_redistribution(state)

    assert len(transfers)==1
    returns=plan_empty_freighter_returns(state,transfers)
    assert len(returns)==1
    assert returns[0].fleet_id==4
    assert returns[0].export_planet_id==0


def test_all_idle_empty_freighters_return_without_an_arbitrary_return_cap():
    state=_state(home_pop=320_000)
    for fid in range(4,10):
        state.fleets.append(_fleet(fid,120,1))

    returns=plan_empty_freighter_returns(state)

    assert len(returns)==6
    assert {x.export_planet_id for x in returns}=={0}


def test_empty_transport_stages_at_ready_exporter_even_before_new_demand_is_known():
    state=_state(home_pop=320_000)
    state.fleets[0].position=Position(120,0)
    state.fleets[0].native["position_object_id"]=1
    # The return is a capacity-positioning decision, so an active export world
    # stays useful even in the turn between one completed demand and the next.
    state.planets=[p for p in state.planets if p.id != 10]

    returns=plan_empty_freighter_returns(state)

    assert returns and returns[0].export_planet_id==0


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
    state=_state(home_pop=270_000); orders=OrderSet("pop",2415,1)
    intents=add_population_redistribution_orders(state,orders)
    assert intents
    order=next(o for o in orders.orders if o.kind=="transport_population")
    assert order.payload["population_kt"]==200
    assert order.payload["gate_allowed_while_loaded"] is False
    assert order.payload["population_dispatch_policy"]=="source_surplus_destination_need_and_hold_with_protected_floor"
    assert order.payload["native_experiment"]["trust_level"]=="EXPERIMENTAL"


def test_population_freight_uses_its_remaining_hold_for_real_mineral_need():
    state=_state(home_pop=270_000)
    home=next(p for p in state.planets if p.id==0)
    child=next(p for p in state.planets if p.id==1)
    home.ironium=700; home.boranium=600; home.germanium=500
    child.ironium=0; child.boranium=0; child.germanium=0
    orders=OrderSet("pop",2415,1)
    orders.add("set_planet_queue",{"planet_id":1,"queue":[
        {"item":"factory","quantity":20},{"item":"defense","quantity":10},
    ]},"test")

    intent=plan_population_redistribution(state,orders=orders)[0]
    assert intent.population_kt==200
    assert sum(intent.mineral_load.values())==50  # 250 kT capacity less 200 kT population.
    assert intent.mineral_load["germanium"]>0
    assert intent.native_experiment["id"]=="population-type2-200kt-with-minerals"
