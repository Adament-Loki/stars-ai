from stars_ai.design_legality import ComponentCategory, ComponentRef, HULL_RULES, validate_design
from stars_ai.models import GameState, Planet, Position, RaceProfile, Tech
from stars_ai.native.design_change import EncodedShipDesign, create_ship_design_blocks, encode_stars_string, parse_design_change_payload
from stars_ai.ship_design_synth import synthesize_freighter_upgrade, synthesize_onion_privateer, safe_recyclable_ship_slot


def test_medium_freighter_name_matches_controlled_stars_encoding():
    assert encode_stars_string("Medium Freighter") == bytes.fromhex("0B BC 2D 64 DE DB 0B 58 24 D8 3A 28")


def test_type27_medium_freighter_fixture_matches_controlled_gui_bytes():
    d=EncodedShipDesign(4,1,4,50,0,(
        ComponentRef(1,2,1),ComponentRef(4096,5,1),ComponentRef(4,0,1)
    ),"Medium Freighter")
    staging,final=create_ship_design_blocks(d)
    assert staging.data==bytes.fromhex(
        "11 A4 07 10 01 04 32 00 03 00 00 00 00 00 00 00 00 00 00 00 "
        "00 00 00 00 00 00 00 00 00 00 00 0B BC 2D 64 DE DB 0B 58 24 D8 3A 28"
    )
    assert final.data==bytes.fromhex(
        "11 64 07 10 01 04 32 00 03 00 00 00 00 00 00 00 00 00 00 01 "
        "00 02 01 00 10 05 01 04 00 00 01 0B BC 2D 64 DE DB 0B 58 24 D8 3A 28"
    )
    parsed=parse_design_change_payload(final.data)
    assert parsed.design_slot==4 and parsed.hull_id==1 and parsed.pic==4


def test_large_freighter_requires_exactly_two_identical_engines():
    hull=HULL_RULES[2]
    empty=[ComponentRef(0,0,0) for _ in hull.slots]
    empty[0]=ComponentRef(int(ComponentCategory.ENGINE),2,1)
    bad=validate_design(2,empty)
    assert not bad.ok and any(x.code=="engine_count_mismatch" for x in bad.issues)
    empty[0]=ComponentRef(int(ComponentCategory.ENGINE),2,2)
    assert validate_design(2,empty).ok


def _lf_state():
    home=Planet(0,"Home",Position(0,0),owner=1,habitability=100,population=400000,ironium=1800,boranium=1000,germanium=900,native={"is_homeworld":True,"starbase_capabilities":{"can_build_ships":True,"can_refuel":True}})
    child=Planet(1,"Child",Position(120,0),owner=1,habitability=80,population=220000,ironium=10,boranium=10,germanium=10,native={"starbase_capabilities":{"can_build_ships":True,"can_refuel":True},"has_starbase":True})
    return GameState("design",2420,1,RaceProfile(native={"lrts":["IFE"]}),Tech(propulsion=2,construction=8),[home,child],[],native={
        "design_profiles":[{"design_number":3,"name":"Medium Freighter","role":"freighter","hull_id":1,"cargo_capacity":210,"fuel_capacity":450,"dry_mass":80,"engine_id":2}],
        "designs":[{"design_number":3,"name":"Medium Freighter","hull_id":1,"is_starbase":False,"total_remaining":0,"turn_designed":0,"slots":[{"category":1,"item_id":2,"count":1},{"category":0,"item_id":0,"count":0},{"category":0,"item_id":0,"count":0}]}],
        "production_by_planet":{"1":[{"item_type":4,"item_id":3,"count":5}]},
    })


def test_synthesized_large_freighter_has_two_fuel_mizers_and_waits_for_readback():
    state=_lf_state(); plan=synthesize_freighter_upgrade(state)
    assert plan is not None
    assert plan.encoded.hull_id==2
    assert plan.encoded.slots[0]==ComponentRef(1,2,2)
    assert all(x.count==0 for x in plan.encoded.slots[1:])
    assert "next-M read-back" in plan.reason



def test_onion_privateer_uses_one_engine_and_three_fuel_tanks():
    state=_lf_state(); state.tech.construction=4
    # Remove bulk condition so the opening transport plan is selected.
    state.native["production_by_planet"]={}
    state.planets[1].population=50_000
    state.planets[1].native={}
    plan=synthesize_onion_privateer(state)
    assert plan is not None
    assert plan.encoded.hull_id==11
    assert plan.encoded.slots[0]==ComponentRef(1,2,1)
    for idx in (2,3,4):
        assert plan.encoded.slots[idx]==ComponentRef(4096,5,1)


def test_safe_slot_never_recycles_live_design():
    state=_lf_state()
    # Fill all slots with dead designs, then mark slot 0 live. Selector may choose another dead slot but never 0.
    state.native["designs"]=[{"design_number":i,"is_starbase":False,"total_remaining":0,"turn_designed":i} for i in range(16)]
    from stars_ai.models import Fleet
    state.fleets=[Fleet(1,"Live",1,Position(0,0),role="freighter",native={"ship_count":[1]+[0]*15})]
    slot=safe_recyclable_ship_slot(state)
    assert slot is not None and slot[0] != 0
