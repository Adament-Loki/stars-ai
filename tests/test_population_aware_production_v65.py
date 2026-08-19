
from stars_ai.models import GameState,RaceProfile,Tech,Planet,Position,OrderSet
from stars_ai.planet_economy import decode_race_economy, installation_caps, installation_status
from stars_ai.strategy.economy import add_economic_orders
from stars_ai.native.x_writer import _production_block


def race(operable_factories=10,operable_mines=10):
    return RaceProfile(
        name="Test",
        primary_trait="Jack of All Trades",
        native={
            "population_efficiency_raw":10,
            "economy_raw":[10,10,operable_factories,10,5,operable_mines],
            "flags_73":0x80,
            "prt_id":9,
        },
    )


def state(pop,factories,mines,current_queue=None,starbase=False):
    p=Planet(
        0,"Home",Position(0,0),owner=1,observed=True,habitability=100,
        population=pop,factories=factories,mines=mines,
        native={"has_starbase":starbase},
    )
    return GameState(
        "g",2400,1,race(),Tech(),[p],[],
        native={
            "design_profiles":[],
            "production_by_planet":{0:(current_queue or [])},
        },
    )


def test_exact_race_economy_byte_mapping():
    e=decode_race_economy(
        RaceProfile(
            primary_trait="Jack of All Trades",
            native={
                "population_efficiency_raw":7,
                "economy_raw":[15,5,25,25,2,25],
                "flags_73":0x80,
                "prt_id":9,
            },
        )
    )
    assert e.colonists_per_resource==700
    assert e.factory_output_per_10==15
    assert e.factory_build_cost==5
    assert e.operable_factories_per_10k==25
    assert e.mine_output_per_10==25
    assert e.mine_build_cost==2
    assert e.operable_mines_per_10k==25
    assert e.factory_germanium_discount is True


def test_population_cap_formula():
    e=decode_race_economy(race(15,12))
    assert installation_caps(100_000,e)==(150,120)
    assert installation_caps(25_000,e)==(37,30)


def test_never_build_factories_beyond_current_population_cap():
    s=state(100_000,100,20)
    o=OrderSet("g",2400,1)
    add_economic_orders(s,o,None)
    q=next(x for x in o.orders if x.kind=="set_planet_queue").payload["queue"]
    assert not any(x["item"]=="factory" for x in q)
    assert any(x["item"]=="mine" for x in q)


def test_never_build_mines_beyond_current_population_cap():
    s=state(100_000,20,100)
    # Give the planet enough Germanium to pay for factories; the mine assertion
    # is what this test is about.
    s.planets[0].germanium=200
    o=OrderSet("g",2400,1)
    add_economic_orders(s,o,None)
    q=next(x for x in o.orders if x.kind=="set_planet_queue").payload["queue"]
    assert any(x["item"]=="factory" for x in q)
    assert not any(x["item"]=="mine" for x in q)


def test_built_infrastructure_quantity_is_clamped_to_headroom():
    s=state(25_000,36,29)
    # 25k @ 10/10 => caps 25/25, so already above cap, no infra build.
    o=OrderSet("g",2400,1)
    add_economic_orders(s,o,None)
    assert not any(
        q["item"] in ("factory","mine")
        for x in o.orders if x.kind=="set_planet_queue"
        for q in x.payload["queue"]
    )


def test_stale_factory_queue_is_cleared_when_caps_reached():
    current=[
        {"item_id":7,"item":"Factory","count":10,"complete_percent":0,"item_type":2},
        {"item_id":8,"item":"Mine","count":10,"complete_percent":0,"item_type":2},
    ]
    s=state(100_000,100,100,current_queue=current)
    o=OrderSet("g",2400,1)
    add_economic_orders(s,o,None)
    pq=next(x for x in o.orders if x.kind=="set_planet_queue")
    assert pq.payload["queue"]==[]
    assert pq.payload["clear_queue"] is True
    assert pq.payload["research_when_idle"] is True


def test_empty_native_production_queue_change_is_planet_id_only():
    b=_production_block(17,[])
    assert b.type_id==29
    assert b.data==(17).to_bytes(2,"little")


def test_growth_opens_more_capacity_next_turn():
    e=decode_race_economy(race(10,10))
    assert installation_caps(100_000,e)==(100,100)
    assert installation_caps(110_000,e)==(110,110)
