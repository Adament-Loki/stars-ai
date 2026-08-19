
# v6.5 Population-Aware Planet Production

## Core rule

Factories and mines are built only when the planet's CURRENT population can
operate them.

Stars! race settings define:

- factories operable per 10,000 colonists
- mines operable per 10,000 colonists

The AI now calculates:

`factory_cap = floor(population * operable_factories / 10,000)`

`mine_cap = floor(population * operable_mines / 10,000)`

No intentional factory or mine build may exceed those caps.

Population growth can open new infrastructure headroom on the next yearly run.

## Exact race economy decoding

StarsAPI PlayerBlock full race data:

- byte 54: population efficiency, hundreds of colonists per resource
- byte 55: resources produced by 10 factories
- byte 56: factory resource cost
- byte 57: factories operable per 10k colonists
- byte 58: minerals produced by 10 mines
- byte 59: mine resource cost
- byte 60: mines operable per 10k colonists
- byte 73 bit 7: factory costs 1kT less Germanium

This replaces v6.4's approximate `population//2500` / `population//3000`
development thresholds.

## Production priority

At a planet with a starbase:

1. preserve an already-started custom ship build
2. build objective ships the empire currently needs
3. build factories only up to current population-supported cap
4. build mines only up to current population-supported cap
5. defenses only when the persona has an explicitly elevated defensive posture
6. otherwise research

Examples:

- 100k population, race operates 10 factories/10k:
  - factory cap = 100
  - if 100 factories exist, build ZERO more factories

- 100k population, 100 factories, 70 mines:
  - no factories
  - up to 30 additional mines can be economically useful

- two known colonizable planets but only one colony ship:
  - shipyard queues another colony ship before additional infrastructure

## Research diversion

If no objective ship or currently operable installation is useful and the
planet still has a stale production queue, v6.5 emits an EMPTY
ProductionQueueChange to clear the queue.

StarsAPI's ProductionQueueChange encoding explicitly supports zero queue items:
the block then consists only of the 2-byte planet ID.

With no useful planetary build consuming resources, Stars! directs unused
production capacity to research.

## Reporting

Planet production reports now show:

`population; factories current/cap; mines current/cap`

and distinguish:

- BUILD ...
- CLEAR QUEUE -> RESEARCH
- RESEARCH / KEEP EMPTY

This makes infrastructure saturation visible in every yearly decision report.
