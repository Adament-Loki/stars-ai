
from stars_ai.native.x_writer import _player_relation_block
from stars_ai.windows_autohost import IntegratedNativeOrderBridge, WindowsAutoHostConfig
from stars_ai.models import GameState,RaceProfile,Tech,Planet,Fleet,Position,OrderSet
from stars_ai.strategy.military import add_military_orders
from stars_ai.persona import BalancedPersona

def test_empirical_player2_to_player1_friend_payload():
    b=_player_relation_block(2, 2, [], {1: "friend"})
    assert b.type_id==38
    assert b.data==bytes.fromhex("01 00")

def test_seven_player_friend_and_enemy_arrays_match_the_client():
    friends=_player_relation_block(1, 7, [], {2: "friend", 3: "friend", 4: "friend"})
    enemies=_player_relation_block(2, 7, [], {1: "enemy", 3: "enemy", 4: "enemy"})
    assert friends.data==bytes.fromhex("00 01 01 01 00 00 00")
    assert enemies.data==bytes.fromhex("02 00 02 02 00 00 00")

def test_confirmed_friend_stays_set_when_another_friend_is_still_pending():
    block=_player_relation_block(1, 7, [0, 1, 0, 0, 0, 0, 0], {3: "friend", 4: "friend"})
    assert block.data==bytes.fromhex("00 01 01 01 00 00 00")

def test_allied_pair_expands_reciprocally():
    b=IntegratedNativeOrderBridge({},None,[[1,2]])
    assert b._friend_ids_for(1)==[2]
    assert b._friend_ids_for(2)==[1]
    assert b._friend_ids_for(3)==[]

def test_multiple_allied_pairs():
    b=IntegratedNativeOrderBridge({},None,[[1,2],[1,4]])
    assert b._friend_ids_for(1)==[2,4]
    assert b._friend_ids_for(2)==[1]
    assert b._friend_ids_for(4)==[1]

def test_friend_native_relation_never_engageable():
    state=GameState(
        "g",2400,1,
        RaceProfile(native={"player_relations":[0,1]}),
        Tech(),
        [Planet(0,"Home",Position(0,0),owner=1,observed=True)],
        [
            Fleet(0,"Our Combat",1,Position(0,0),role="combat",combat_power=100,speed=8),
            Fleet(1,"Friend Fleet",2,Position(10,0),role="combat",combat_power=1,speed=8),
        ],
    )
    plan=BalancedPersona().build_plan(state)
    orders=OrderSet("g",2400,1)
    add_military_orders(state,orders,plan)
    assert not any(o.kind=="move_fleet" for o in orders.orders)

def test_config_schema_accepts_allied_pairs():
    cfg=WindowsAutoHostConfig(
        stars_exe="stars.exe",seed_dir="seed",output_dir="out",game_name="GAME",
        allied_pairs=[[1,2]]
    )
    assert cfg.allied_pairs==[[1,2]]
