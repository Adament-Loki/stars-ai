
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable
import json
import statistics

from .game_observer import GameObserver, GameRecap

@dataclass
class AgentSlot:
    player_id: int
    persona: str
    objectives: list[dict[str, Any]] = field(default_factory=list)
    human: bool = False

@dataclass
class SelfPlayConfig:
    game_name: str
    max_turns: int = 200
    checkpoints: list[int] = field(default_factory=lambda: [25, 50, 100, 150, 200])
    seed: int = 1
    agents: list[AgentSlot] = field(default_factory=list)

@dataclass
class RunResult:
    game_name: str
    seed: int
    turns_completed: int
    checkpoints: dict[int, dict[str, Any]]
    winner_player_id: int | None
    eliminated_players: list[int]
    anomalies: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

def score_summary(s) -> float:
    # Transparent composite metric for test/tournament comparison, not game truth.
    return (
        6.0 * s.owned_planets
        + s.population / 25000.0
        + s.factories / 20.0
        + s.mines / 30.0
        + 0.8 * s.visible_ship_count
        + s.visible_fleet_mass / 1200.0
        + 1.2 * s.tech_sum
    )

def determine_leader(recap: GameRecap) -> int | None:
    living = [s for s in recap.player_summaries if s.owned_planets > 0 or s.population > 0]
    if not living:
        return None
    return max(living, key=score_summary).player_id

def detect_anomalies(recap: GameRecap) -> list[str]:
    out = []
    for s in recap.player_summaries:
        if s.owned_planets > 0 and s.population <= 0:
            out.append(f"P{s.player_id}: owns planets but has zero visible population")
        if s.factories < 0 or s.mines < 0 or s.visible_ship_count < 0:
            out.append(f"P{s.player_id}: impossible negative state")
        if s.owned_planets >= 8 and s.factories == 0:
            out.append(f"P{s.player_id}: expanded to {s.owned_planets} planets with zero factories")
        if s.tech_sum == 0 and s.owned_planets >= 5:
            out.append(f"P{s.player_id}: established empire without visible research progression")
    for d in recap.player_deltas:
        if d.planets_delta <= -5:
            out.append(f"P{d.player_id}: catastrophic territorial collapse ({d.planets_delta} planets)")
        if d.ship_count_delta <= -25:
            out.append(f"P{d.player_id}: catastrophic fleet collapse ({d.ship_count_delta} ships)")
    return out

class SelfPlayRunner:
    """
    Engine-agnostic tournament controller.

    The host adapter supplies:
      create_game(config, run_dir)
      play_player_turn(game, AgentSlot, turn, run_dir)
      host_advance(game, turn, run_dir)
      read_host_state(game, turn, run_dir)
      is_finished(game, turn) -> bool

    This keeps the test harness separate from the Stars! executable automation.
    """

    def __init__(self, host_adapter: Any):
        self.host = host_adapter
        self.observer = GameObserver()

    def run(self, config: SelfPlayConfig, output_dir: str | Path) -> RunResult:
        run_dir = Path(output_dir) / f"{config.game_name}-seed-{config.seed}"
        run_dir.mkdir(parents=True, exist_ok=True)

        game = self.host.create_game(config, run_dir)
        previous_state = None
        checkpoint_data = {}
        anomalies = []
        turns_completed = 0

        for turn in range(1, config.max_turns + 1):
            for agent in config.agents:
                if agent.human:
                    continue
                self.host.play_player_turn(game, agent, turn, run_dir)

            self.host.host_advance(game, turn, run_dir)
            state = self.host.read_host_state(game, turn, run_dir)
            turns_completed = turn

            if turn in config.checkpoints:
                recap = self.observer.recap(state, previous_state=previous_state, turn=turn)
                checkpoint_data[turn] = recap.to_dict()
                anomalies.extend(detect_anomalies(recap))
                cp = run_dir / "checkpoints"
                cp.mkdir(exist_ok=True)
                (cp / f"turn-{turn:03d}.json").write_text(
                    json.dumps(recap.to_dict(), indent=2),
                    encoding="utf-8"
                )

            previous_state = state
            if self.host.is_finished(game, turn):
                break

        final_recap = self.observer.recap(previous_state, turn=turns_completed)
        winner = determine_leader(final_recap)
        eliminated = [
            s.player_id for s in final_recap.player_summaries
            if s.owned_planets <= 0 and s.population <= 0
        ]

        result = RunResult(
            game_name=config.game_name,
            seed=config.seed,
            turns_completed=turns_completed,
            checkpoints=checkpoint_data,
            winner_player_id=winner,
            eliminated_players=eliminated,
            anomalies=sorted(set(anomalies)),
        )
        (run_dir / "run-result.json").write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        return result

@dataclass
class PersonaAggregate:
    persona: str
    games: int
    wins: int
    win_rate: float
    avg_final_planets: float
    avg_final_population: float
    avg_final_factories: float
    avg_final_ships: float
    avg_final_tech_sum: float
    eliminations: int
    anomaly_count: int

@dataclass
class TournamentReport:
    runs: list[RunResult]
    personas: list[PersonaAggregate]

    def to_dict(self):
        return {
            "runs": [r.to_dict() for r in self.runs],
            "personas": [asdict(p) for p in self.personas],
        }

    def render_text(self) -> str:
        lines = ["Stars! Self-Play Tournament Report", "==================================", ""]
        for p in sorted(self.personas, key=lambda x: x.win_rate, reverse=True):
            lines.append(
                f"{p.persona}: {p.wins}/{p.games} wins ({p.win_rate:.1%}), "
                f"avg planets {p.avg_final_planets:.1f}, pop {p.avg_final_population:,.0f}, "
                f"factories {p.avg_final_factories:.1f}, ships {p.avg_final_ships:.1f}, "
                f"techΣ {p.avg_final_tech_sum:.1f}, eliminations {p.eliminations}, "
                f"anomalies {p.anomaly_count}"
            )
        return "\n".join(lines) + "\n"

class TournamentRunner:
    def __init__(self, selfplay_runner: SelfPlayRunner):
        self.runner = selfplay_runner

    def run_many(
        self,
        base_config: SelfPlayConfig,
        seeds: list[int],
        output_dir: str | Path,
    ) -> TournamentReport:
        results = []
        persona_by_player = {a.player_id: a.persona for a in base_config.agents}
        for seed in seeds:
            cfg = SelfPlayConfig(
                game_name=base_config.game_name,
                max_turns=base_config.max_turns,
                checkpoints=list(base_config.checkpoints),
                seed=seed,
                agents=list(base_config.agents),
            )
            results.append(self.runner.run(cfg, output_dir))

        buckets = {}
        for persona in set(persona_by_player.values()):
            buckets[persona] = {
                "games": 0, "wins": 0, "planets": [], "population": [],
                "factories": [], "ships": [], "tech": [],
                "eliminations": 0, "anomalies": 0
            }

        for r in results:
            final_cp = None
            if r.checkpoints:
                final_cp = r.checkpoints[max(r.checkpoints)]
            if final_cp is None:
                continue
            summaries = final_cp["player_summaries"]
            for s in summaries:
                pid = s["player_id"]
                persona = persona_by_player.get(pid)
                if persona is None:
                    continue
                b = buckets[persona]
                b["games"] += 1
                b["wins"] += int(r.winner_player_id == pid)
                b["planets"].append(s["owned_planets"])
                b["population"].append(s["population"])
                b["factories"].append(s["factories"])
                b["ships"].append(s["visible_ship_count"])
                b["tech"].append(s["tech_sum"])
                b["eliminations"] += int(pid in r.eliminated_players)
                b["anomalies"] += len(r.anomalies)

        aggregates = []
        for persona, b in buckets.items():
            g = b["games"]
            aggregates.append(PersonaAggregate(
                persona=persona,
                games=g,
                wins=b["wins"],
                win_rate=(b["wins"]/g if g else 0.0),
                avg_final_planets=(statistics.mean(b["planets"]) if b["planets"] else 0.0),
                avg_final_population=(statistics.mean(b["population"]) if b["population"] else 0.0),
                avg_final_factories=(statistics.mean(b["factories"]) if b["factories"] else 0.0),
                avg_final_ships=(statistics.mean(b["ships"]) if b["ships"] else 0.0),
                avg_final_tech_sum=(statistics.mean(b["tech"]) if b["tech"] else 0.0),
                eliminations=b["eliminations"],
                anomaly_count=b["anomalies"],
            ))

        report = TournamentReport(results, aggregates)
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "tournament-report.json").write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        (out / "tournament-report.txt").write_text(report.render_text(), encoding="utf-8")
        return report
