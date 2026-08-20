from stars_ai.models import GameState, Planet, Position, RaceProfile, Tech
from stars_ai.expansion_research import expansion_research_demands


def _state(bulk=False):
    race=RaceProfile(growth_rate=.15,native={"lrts":["IFE","ISB"]})
    home=Planet(0,"Home",Position(0,0),owner=1,habitability=100,population=120000,
        ironium=1800 if bulk else 300,boranium=1000 if bulk else 220,germanium=900 if bulk else 220,
        native={"is_homeworld":True,"starbase_capabilities":{"can_build_ships":True,"can_refuel":True}})
    child=Planet(1,"Child",Position(130,0),owner=1,habitability=80,population=50000,
        ironium=20,boranium=15,germanium=10,native=(
            {"has_starbase":True,"starbase_capabilities":{"can_build_ships":True,"can_refuel":True}} if bulk else {}
        ))
    frontier=[Planet(10+i,f"F{i}",Position(170+i*8,10),owner=None,habitability=75,observed=True) for i in range(5)]
    production={"1":[{"item_type":4,"item_id":3,"count":5}]} if bulk else {}
    return GameState("r",2424,1,race,Tech(propulsion=2,construction=5),[home,child,*frontier],[],native={
        "design_profiles":[{"design_number":1,"name":"MF","role":"freighter","cargo_capacity":210,"fuel_capacity":450,"dry_mass":80,"engine_id":2}],
        "production_by_planet":production,
    })


def test_population_backlog_alone_does_not_make_large_freighter_a_research_goal():
    names={d["name"] for d in expansion_research_demands(_state(False))}
    assert "Large Freighter" not in names


def test_bulk_shipyard_pressure_can_make_large_freighter_a_research_goal():
    ds=expansion_research_demands(_state(True))
    lf=next(d for d in ds if d["name"]=="Large Freighter")
    assert lf["category"]=="industrial_logistics"
    assert "Population backlog alone does not trigger C8" in lf["explanation"]
