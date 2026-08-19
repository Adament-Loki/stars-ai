
from dataclasses import dataclass
from stars_ai.selfplay import (
    AgentSlot, SelfPlayConfig, SelfPlayRunner, TournamentRunner,
)

@dataclass
class Planet:
    owner_id: int
    population: int
    factories: int
    mines: int
    defenses: int = 0

@dataclass
class Fleet:
    owner_id: int
    ship_count: list[int]
    mass: int

@dataclass
class Player:
    player_id: int
    tech: dict

class State:
    def __init__(self, planets, fleets, players):
        self.planets=planets
        self.fleets=fleets
        self.players=players

class FakeHost:
    def create_game(self, config, run_dir):
        return {"seed": config.seed}

    def play_player_turn(self, game, agent, turn, run_dir):
        pass

    def host_advance(self, game, turn, run_dir):
        pass

    def read_host_state(self, game, turn, run_dir):
        seed=game["seed"]
        # Expansionist P1 grows faster early; Militarist P2 catches via ships.
        p1_planets = 1 + turn//10
        p2_planets = 1 + turn//12
        return State(
            [Planet(1, 20000*p1_planets, 8*p1_planets, 6*p1_planets) for _ in range(p1_planets)]
            + [Planet(2, 18000*p2_planets, 7*p2_planets, 6*p2_planets) for _ in range(p2_planets)],
            [Fleet(1,[2+turn//8],200+turn*8), Fleet(2,[3+turn//6],250+turn*10)],
            [Player(1,{"propulsion":3+turn//20}), Player(2,{"weapons":3+turn//20})]
        )

    def is_finished(self, game, turn):
        return False

def test_selfplay_checkpoints(tmp_path):
    cfg=SelfPlayConfig(
        game_name="test",
        max_turns=50,
        checkpoints=[25,50],
        seed=1,
        agents=[AgentSlot(1,"Expansionist"),AgentSlot(2,"Militarist")],
    )
    r=SelfPlayRunner(FakeHost()).run(cfg,tmp_path)
    assert 25 in r.checkpoints
    assert 50 in r.checkpoints
    assert r.turns_completed==50
    assert r.winner_player_id in (1,2)

def test_tournament_aggregates(tmp_path):
    cfg=SelfPlayConfig(
        game_name="test",
        max_turns=50,
        checkpoints=[50],
        agents=[AgentSlot(1,"Expansionist"),AgentSlot(2,"Militarist")],
    )
    tr=TournamentRunner(SelfPlayRunner(FakeHost())).run_many(cfg,[1,2,3],tmp_path)
    assert len(tr.runs)==3
    assert {p.persona for p in tr.personas}=={"Expansionist","Militarist"}
    assert all(p.games==3 for p in tr.personas)
