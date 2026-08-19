from stars_ai.agent import StarsAgent
from stars_ai.diplomacy import DiplomacyPolicy, PlayerAttitude
from stars_ai.models import GameState, RaceProfile, Tech, Planet, Fleet, Position
from stars_ai.persona import BalancedPersona


def make_state(friend_relation=True):
    # relations indexed by player slot: P1 self, P2 friend, P3 enemy
    rel = [0, 1 if friend_relation else 0, 2]
    return GameState(
        game_name='dip-test', year=2450, player_id=1,
        race=RaceProfile(name='AI', native={'player_relations': rel}),
        tech=Tech(),
        planets=[Planet(0, 'Home', Position(100,100), owner=1, observed=True)],
        fleets=[
            Fleet(0,'Ours',1,Position(100,100),role='combat',combat_power=1000),
            Fleet(1,'P2',2,Position(300,300),role='unknown',combat_power=250),
            Fleet(2,'P3',3,Position(110,105),role='unknown',combat_power=500),
        ]
    )


def test_human_player_can_never_ally():
    state = make_state()
    policy = DiplomacyPolicy(human_player_ids=frozenset({2}))
    view = policy.evaluate_player(state, 2)
    assert view.is_human is True
    assert view.can_ally is False
    assert view.attitude != PlayerAttitude.ALLIED


def test_ai_friend_can_ally():
    state = make_state()
    policy = DiplomacyPolicy(human_player_ids=frozenset())
    view = policy.evaluate_player(state, 2)
    assert view.can_ally is True
    assert view.attitude in (PlayerAttitude.HELPFUL, PlayerAttitude.ALLIED)


def test_agent_never_emits_friend_for_human():
    state = make_state()
    persona = BalancedPersona().with_human_players(2)
    orders = StarsAgent(state, persona=persona).play_turn()
    friend_orders = [o for o in orders.orders if o.kind == 'set_player_relation' and o.payload.get('relation') == 'friend']
    assert all(o.payload.get('player_id') != 2 for o in friend_orders)


def test_enemy_near_home_is_hostile_and_conflict_assessed():
    state = make_state()
    policy = DiplomacyPolicy(human_player_ids=frozenset({2}))
    view = policy.evaluate_player(state, 3)
    assert view.attitude == PlayerAttitude.HOSTILE
    assert view.threat >= 0.62
    assert view.conflict.strategic_risk >= 0


def test_helpful_human_fleet_is_not_treated_as_military_target():
    state = make_state()
    # Remove P3 so only the helpful human P2 remains as another player's fleet.
    state.fleets = [f for f in state.fleets if f.owner != 3]
    state.fleets[1].position = Position(105, 105)
    persona = BalancedPersona().with_human_players(2)
    orders = StarsAgent(state, persona=persona).play_turn()
    military = [o for o in orders.orders if o.kind == 'move_fleet' and o.payload.get('mission') in ('defend','attack')]
    assert not military
