
# Self-Play Tournament Harness v2.8

Purpose: run AI personas against one another repeatedly and use the games as
strategy tests, regression tests, and debugging data.

## Isolation

Each Stars! player should have an isolated directory:

```
run-001/
  host/
  player-01/
  player-02/
  player-03/
  ...
  checkpoints/
```

A player process only sees its own `.m#`, `.xy`, prior memory, and its own
decision trace. The observer/host process alone can read `.hst`.

## Checkpoints

Default:
- turn 25
- turn 50
- turn 100
- turn 150
- turn 200

At each checkpoint capture:
- planets
- population
- factories/mines
- fleet/ship strength
- cumulative tech
- territorial changes
- decision traces
- anomalies

## Tournament metrics

Across many seeds:
- win rate by persona
- elimination rate
- average territory
- average population
- average factories
- average ship count
- average tech sum
- anomaly count

This lets code changes be evaluated empirically. For example:

```
v2.9 Militarist vs v2.8 Militarist:
  +8% win rate
  -15% catastrophic early wars
  +11% population at turn 100
```

or detect a regression:

```
Expansionist after research-planner change:
  turn-50 planets: 8.2 -> 5.6
  propulsion tech: +2.1
  factories: -31%
  likely over-investing in research
```

## Host Adapter

`SelfPlayRunner` is intentionally engine-agnostic. A Windows Stars! adapter
still needs to implement:

- create_game()
- play_player_turn()
- host_advance()
- read_host_state()
- is_finished()

That adapter is the piece that will invoke Stars!, copy `.m#/.x#` files into
isolated player folders, run each AI, and advance the host turn.
