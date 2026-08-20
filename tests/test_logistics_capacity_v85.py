from stars_ai.models import GameState, Planet, Position, RaceProfile, Tech
from stars_ai.logistics_capacity import evaluate_logistics_capacity


def _base_state():
    race=RaceProfile(growth_rate=.15,native={"lrts":["IFE"]})
    home=Planet(0,"Home",Position(0,0),owner=1,habitability=100,population=100_000,
        ironium=300,boranium=220,germanium=220,native={"is_homeworld":True,"starbase_capabilities":{"can_build_ships":True,"can_refuel":True}})
    child=Planet(1,"Child",Position(120,0),owner=1,habitability=80,population=50_000,
        ironium=20,boranium=20,germanium=20,native={})
    frontier=[Planet(10+i,f"F{i}",Position(150+i*5,20),owner=None,habitability=70,observed=True) for i in range(5)]
    return GameState("log",2415,1,race,Tech(construction=7,propulsion=2),[home,child,*frontier],[],native={"production_by_planet":{}})


def test_population_backlog_sizes_compact_roundtrip_fleet_not_large_freighter():
    state=_base_state()
    snap=evaluate_logistics_capacity(state)
    assert snap.population_lane_count>=1
    assert 1 <= snap.desired_population_freighters <= 4
    assert snap.large_freighter_valuable is False
    assert snap.desired_bulk_freighters==0


def test_bulk_shipyard_pressure_is_what_makes_large_freighter_valuable():
    state=_base_state(); state.year=2430
    home=state.planets[0]
    home.ironium=1600; home.boranium=1000; home.germanium=900
    yard=state.planets[1]
    yard.population=220_000
    yard.ironium=20; yard.boranium=15; yard.germanium=10
    yard.native={"has_starbase":True,"starbase_capabilities":{"can_build_ships":True,"can_refuel":True}}
    state.native["production_by_planet"]={"1":[{"item_type":4,"item_id":3,"count":5}]}
    snap=evaluate_logistics_capacity(state)
    assert snap.bulk_transferable_kt>=600
    assert snap.large_freighter_valuable is True
    assert snap.desired_bulk_freighters>=1
