from stars_ai.models import GameState, Planet, Position, RaceProfile, Tech
from stars_ai.objective_production import _pick, plan_objective_ship_builds


def _state(bulk=False):
    home=Planet(0,"Home",Position(0,0),owner=1,habitability=100,population=120000,
        ironium=1600 if bulk else 300,boranium=1000 if bulk else 220,germanium=900 if bulk else 220,
        native={"is_homeworld":True,"starbase_capabilities":{"can_build_ships":True,"can_refuel":True}})
    child=Planet(1,"Child",Position(130,0),owner=1,habitability=80,population=50000,
        ironium=20,boranium=15,germanium=10,native=(
            {"has_starbase":True,"starbase_capabilities":{"can_build_ships":True,"can_refuel":True}} if bulk else {}
        ))
    frontier=[Planet(10+i,f"F{i}",Position(160+i*5,10),owner=None,habitability=75,observed=True) for i in range(4)]
    profiles=[
        {"design_number":1,"name":"Onion Privateer","role":"freighter","hull_id":11,"cargo_capacity":250,"fuel_capacity":1400,"dry_mass":77,"engine_id":2},
        {"design_number":2,"name":"LF","role":"freighter","hull_id":2,"cargo_capacity":1200,"fuel_capacity":2600,"dry_mass":137,"engine_id":2},
    ]
    production={"1":[{"item_type":4,"item_id":3,"count":5}]} if bulk else {}
    return GameState("obj",2430 if bulk else 2418,1,RaceProfile(growth_rate=.15,native={"lrts":["IFE"]}),Tech(construction=8,propulsion=2),[home,child,*frontier],[],native={
        "design_profiles":profiles,"production_by_planet":production,"strategic_watchdog":{"colonization_pressure":1.4,"exploration_pressure":1.0}
    })


def test_general_freighter_pick_for_population_prefers_compact_privateer():
    assert _pick(_state(False),"freighter")["name"]=="Onion Privateer"


def test_population_demand_builds_compact_transport_not_large_freighter():
    req=plan_objective_ship_builds(_state(False))
    freight=[x for x in req if x.role=="freighter"]
    # Existing assets may already satisfy demand; if a build is needed it must be compact.
    assert all(x.design_slot==1 for x in freight)


def test_bulk_industrial_pressure_can_request_large_freighter():
    req=plan_objective_ship_builds(_state(True))
    freight=[x for x in req if x.role=="freighter"]
    assert any(x.design_slot==2 for x in freight)
