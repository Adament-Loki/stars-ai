from pathlib import Path
import json

from stars_ai.design_legality import ComponentCategory, ComponentRef, HULL_RULES, validate_design
from stars_ai.native.design_change import EncodedShipDesign, create_ship_design_blocks
from stars_ai.models import GameState, Planet, Position, RaceProfile, Tech
from stars_ai.ship_design_synth import synthesize_onion_privateer, synthesize_role_design
from stars_ai.starsapi_items import stock_hulls, design_mass_and_fuel, proven_available_components
from stars_ai.turn_archive import archive_turn_phase


def _base_state(*,prt_id=7,lrts=None):
    race=RaceProfile(growth_rate=.15,native={"prt_id":prt_id,"lrts":list(lrts or ["IFE"])})
    home=Planet(0,"Home",Position(0,0),owner=1,habitability=100,population=200_000,native={"is_homeworld":True})
    designs=[
        {"design_number":0,"name":"Escort","hull_id":5,"is_starbase":False,"total_remaining":0,"turn_designed":0,
         "slots":[{"category":1,"item_id":2,"count":1},{"category":16,"item_id":4,"count":1},{"category":4,"item_id":2,"count":1},{"category":2048,"item_id":2,"count":1}]},
        {"design_number":1,"name":"Old MF","hull_id":1,"is_starbase":False,"total_remaining":0,"turn_designed":0,
         "slots":[{"category":1,"item_id":2,"count":1},{"category":0,"item_id":0,"count":0},{"category":0,"item_id":0,"count":0}]},
    ]
    return GameState("v88",2420,1,race,Tech(energy=8,weapons=8,propulsion=8,construction=10,electronics=8,biotechnology=5),[home],[],native={"designs":designs,"design_profiles":[{"design_number":0,"role":"combat","hull_id":5,"engine_id":2},{"design_number":1,"role":"freighter","hull_id":1,"engine_id":2,"cargo_capacity":210,"fuel_capacity":400,"dry_mass":80}],"production_by_planet":{}})


def test_all_37_hulls_share_one_canonical_geometry():
    hulls=stock_hulls()
    assert set(hulls)==set(range(37))
    assert all(h.slot_count==len(HULL_RULES[hid].slots) for hid,h in hulls.items())


def test_privateer_geometry_and_onion_mass_fuel_are_exact():
    h=stock_hulls()[11]
    assert h.slot_count==5
    assert h.slots[0].allowed_categories==1 and h.slots[0].capacity==1
    assert h.slots[1].allowed_categories==0x000C and h.slots[1].capacity==2
    assert all(h.slots[i].allows(0x1000) for i in (2,3,4))
    slots=(ComponentRef(1,2,1),ComponentRef(0,0,0),ComponentRef(0x1000,5,1),ComponentRef(0x1000,5,1),ComponentRef(0x1000,5,1))
    assert validate_design(11,slots).ok
    mass,fuel,exact=design_mass_and_fuel(11,slots)
    assert (mass,fuel,exact)==(80,1400,True)


def test_general_combat_builder_remixes_only_proven_components():
    state=_base_state()
    plan=synthesize_role_design(state,"combat",desired_hull_id=7)
    assert plan is not None
    assert plan.encoded.hull_id==7
    proven=set(proven_available_components(state))
    assert any(s.category in (16,32) and s.count for s in plan.encoded.slots)
    for s in plan.encoded.slots:
        if s.count:
            assert (s.category,s.item_id) in proven
    assert validate_design(plan.encoded.hull_id,plan.encoded.slots).ok


def test_prt_restricted_hull_is_not_created_for_wrong_race():
    state=_base_state(prt_id=7)
    assert synthesize_role_design(state,"combat",desired_hull_id=10) is None  # Dreadnought is WM-only in StarsAPI


def test_archive_json_index_can_be_enabled_or_disabled(tmp_path:Path):
    game=tmp_path/'game'; game.mkdir(); (game/'GAME.hst').write_bytes(b'hst')
    root=tmp_path/'archive'
    archive_turn_phase(root,turn_tag='turn-001',phase='00-pre-write',game_dir=game,basename='GAME',json_index=True)
    index=json.loads((root/'index.json').read_text())
    turn=json.loads((root/'turn-001'/'turn.json').read_text())
    assert index['turns']['turn-001']['phase_count']==1
    assert '00-pre-write' in turn['phases']
    root2=tmp_path/'archive2'
    archive_turn_phase(root2,turn_tag='turn-001',phase='00-pre-write',game_dir=game,basename='GAME',json_index=False)
    assert not (root2/'index.json').exists()

from stars_ai.adapters.stars_native import NativeBlock
from stars_ai.native.x_writer import _frame_order_stream


def test_type27_client_framing_is_design_pair_only_without_submit():
    submit=NativeBlock(46,4,bytes.fromhex('01 01 05 19'))
    combined=[NativeBlock(27,2,b'aa'),NativeBlock(27,2,b'bb')]
    client=_frame_order_stream(combined,[submit],type27_client_framing=True)
    assert [b.type_id for b in client]==[27,27]
    ordinary=_frame_order_stream([NativeBlock(34,2,b'cc')],[submit],type27_client_framing=False)
    assert [b.type_id for b in ordinary]==[34,46,46,46]


def test_type27_client_framing_does_not_leak_ordinary_order_blocks():
    submit=NativeBlock(46,4,bytes.fromhex('01 01 05 19'))
    client=_frame_order_stream(
        [NativeBlock(27,2,b'aa'),NativeBlock(27,2,b'bb')],
        [submit],type27_client_framing=True,
    )
    assert [b.type_id for b in client]==[27,27]


def test_permissive_type27_framing_keeps_ordinary_orders_and_submits():
    submit=NativeBlock(46,4,bytes.fromhex('01 01 05 19'))
    mixed=_frame_order_stream(
        [NativeBlock(34,2,b'cc'),NativeBlock(27,2,b'aa'),NativeBlock(27,2,b'bb')],
        [submit],type27_client_framing=False,
    )
    assert [b.type_id for b in mixed]==[34,27,27,46,46,46]


def test_type27_human_engine_only_privateer_control_fixture():
    control=EncodedShipDesign(
        slot=4,hull_id=11,pic=44,armor=150,turn_designed=2,
        slots=(
            ComponentRef(1,2,1),ComponentRef(0,0,0),ComponentRef(0,0,0),
            ComponentRef(0,0,0),ComponentRef(0,0,0),
        ),
        name="TestP",staging_name="Privateer",
    )
    staging,final=create_ship_design_blocks(control,player_id=1)
    assert staging.data == bytes.fromhex(
        "01 a4 07 10 0b 2c 96 00 05 02 00 00 00 00 00 00 00 00 00 "
        "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
        "06 bf 84 df 1a 22 8f"
    )
    assert final.data == bytes.fromhex(
        "01 a4 07 10 0b 2c 96 00 05 02 00 00 00 00 00 00 00 00 00 "
        "01 00 02 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
        "04 c3 29 ab ff"
    )
