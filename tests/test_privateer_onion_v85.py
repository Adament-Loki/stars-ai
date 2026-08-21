from stars_ai.design_legality import ComponentCategory, ComponentRef
from stars_ai.models import GameState, Planet, Position, RaceProfile, Tech
from stars_ai.ship_design_synth import synthesize_onion_privateer


def _state():
    race=RaceProfile(name="Smaugarian",growth_rate=.15,native={"lrts":["IFE"]})
    home=Planet(0,"Home",Position(0,0),owner=1,habitability=100,population=120_000,native={"is_homeworld":True,"starbase_capabilities":{"can_build_ships":True,"can_refuel":True}})
    child=Planet(1,"Child",Position(120,0),owner=1,habitability=80,population=40_000,native={})
    frontier=[Planet(10+i,f"F{i}",Position(150+i*5,20),owner=None,habitability=70,observed=True) for i in range(5)]
    return GameState("priv",2412,1,race,Tech(propulsion=2,construction=4),[home,child,*frontier],[],native={
        "design_profiles":[{"design_number":1,"name":"Old MF","role":"freighter","hull_id":1,"cargo_capacity":210,"fuel_capacity":450,"dry_mass":80,"engine_id":1}],
        "designs":[{"design_number":1,"name":"Old MF","hull_id":1,"is_starbase":False,"total_remaining":0,"turn_designed":0,"slots":[{"category":1,"item_id":1,"count":1},{"category":0,"item_id":0,"count":0},{"category":0,"item_id":0,"count":0}]}],
        "production_by_planet":{},
    })


def test_onion_privateer_is_fuel_mizer_plus_three_basic_fuel_tanks():
    plan=synthesize_onion_privateer(_state())
    assert plan is not None
    assert plan.encoded.hull_id==11
    assert plan.encoded.staging_name=="Privateer"
    assert plan.encoded.name=="Onion Privateer"
    assert plan.encoded.slots[0]==ComponentRef(int(ComponentCategory.ENGINE),2,1)
    for idx in (2,3,4):
        assert plan.encoded.slots[idx]==ComponentRef(int(ComponentCategory.MECHANICAL),5,1)
    assert plan.encoded.slots[1].count==0
    assert "1,400" in plan.reason or "1400" in plan.reason
    assert "20,000-colonist" in plan.reason
