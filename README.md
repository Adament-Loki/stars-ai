# Stars! AI Player — Version 1

This repository is a runnable first version of an AI-player framework for **Stars!**.

## What V1 does

V1 intentionally separates **game-file I/O** from **AI decision-making**.

It currently supports:

- A normalized JSON game-state format.
- One AI process per Stars! player.
- Persistent player memory across turns.
- Deterministic heuristic decisions for:
  - colonization,
  - population movement,
  - planet production,
  - research allocation,
  - scouting,
  - simple military response.
- A host/controller command that can run several AI players for the same turn.
- JSON order output.
- A clean adapter interface where a real `.m#` reader and `.x#` writer can be added.
- A dry-run mode suitable for development and testing.

## Important limitation

This V1 does **not yet write native Stars! `.x#` turn files**.

That support belongs in `src/stars_ai/adapters/stars_binary.py`. The AI engine does not need to change when the native turn-file adapter is added.

This was an intentional design decision: direct binary-file support should only be enabled after the exact Stars! file-format implementation has been validated against real test turns.

## Windows quick start

Install Python 3.11+.

From PowerShell:

```powershell
cd C:\Dev\repos\stars-ai-v1
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Run the included example:

```powershell
stars-ai play-turn `
  --state .\examples\player2-turn2405.json `
  --player 2 `
  --out .\out\player2-orders.json `
  --memory .\state\player2-memory.json
```

Inspect the generated orders:

```powershell
Get-Content .\out\player2-orders.json
```

Run all AI players found in a game manifest:

```powershell
stars-ai host-turn --manifest .\examples\game-manifest.json
```

## Recommended Windows deployment

```text
C:\StarsAI\
├── app\
├── games\
│   └── Orion\
│       ├── host\
│       ├── inbox\
│       │   ├── player2\
│       │   ├── player3\
│       │   └── player4\
│       ├── outbox\
│       └── state\
└── logs\
```

The future native Stars! adapter should:

1. Read `game.m2` + `game.xy`.
2. Convert them into `GameState`.
3. Run the AI.
4. Convert `OrderSet` into a legal `game.x2`.
5. Never expose another player's hidden information to the agent.

## Architecture

```text
Stars! player turn file
        |
        v
   Turn Adapter
        |
        v
    GameState
        |
        v
   AI Player
   /   |    \
econ research military
   \   |    /
    OrderSet
        |
        v
   Turn Adapter
        |
        v
Stars! order file
```

## V1 strategy philosophy

The V1 agent is intentionally conservative:

- Favor strong green planets.
- Expand while available high-quality planets exist.
- Move population from crowded worlds to useful colonies.
- Build basic infrastructure before excessive defenses.
- Maintain research rather than allowing production to consume everything.
- Scout unknown or weakly observed worlds.
- Respond to visible hostile fleets near owned planets.
- Do not use hidden host information.

The goal of V1 is **correct architecture and complete autonomous turn generation**, not grandmaster-level play yet.

## Next milestone

V2 should focus on:

1. Native `.m#` parsing.
2. Native `.x#` order writing or UI-backed order submission.
3. Real Stars! economy formulas.
4. Race-aware factory/mine/population optimization.
5. Ship design and battle simulation.
6. AI-vs-AI regression testing.

## V1.1 native Stars! milestone

The project now includes a Python 3-native Stars! binary reader in:

`src/stars_ai/adapters/stars_native.py`

New commands:

```powershell
stars-ai inspect-stars --mfile .\AI.m1 --xy .\AI.xy --out .\AI-state.json
stars-ai inspect-orders --xfile .\AI.x1 --xy .\AI.xy --out .\AI-orders.json
```

Validated against a real year-2400 Stars! fixture. The reader currently decodes:

- Stars! encrypted block stream and file headers
- `.xy` galaxy metadata and planet coordinates
- full/partial planet records
- full/partial fleet records
- fleet waypoints
- basic player counts
- `.x#` waypoint-add orders
- `.x#` waypoint-task changes
- `.x#` production queue changes
- planet-change and Save & Submit blocks

Known fixture results:

- Homeworld: Magellan (planet 29 in the UI)
- 25,000 population
- 10 mines / 10 factories / 10 defenses
- 6 fleets
- Fleet 1 -> Serapa, Warp 7
- Fleet 2 -> Quiche, Warp 6
- Fleet 3 -> Knob, Warp 9
- Fleet 6 waypoint task 3 = Remote Mining (confirmed by controlled human input)
- Production queue change: 5 Mines + 2 Factories

### Still intentionally disabled

Native `.x#` **writing** is not yet enabled. We have proven that native state and submitted orders can be decoded. The next milestone is to encode a minimal safe `.x#` order set and validate it by opening/submitting it in Stars! before allowing the AI to generate arbitrary native turns.


## Ship-design legality guard

`stars_ai.design_legality` validates proposed ship loadouts before native order writing.
It checks the hull slot count, allowed component category for each slot, slot quantity
capacity, required slots, and a conservative per-player component-availability set.

The current profiles intentionally fail closed: Scout and Destroyer rules are loaded
from combinations already established by known-good game designs. Unknown hulls or
unproven category/slot combinations are rejected rather than guessed. The module is
structured so the complete standard Stars! MOD hull table can be imported as the
next expansion without changing callers.


## Comprehensive ship-design legality (v1.3)

The native design validator now carries the complete stock Stars! hull legality matrix:

- 32 ship hulls
- 5 starbase hulls
- all 37 designable stock hull types
- exact native slot category bitmasks
- exact per-slot component capacities
- required engine-slot enforcement for ships
- combined/general-purpose slot masks preserved exactly
- fail-closed validation for unknown components when a player-availability set is supplied

`stars_ai.design_catalog.hull_catalog()` exposes the matrix as structured data.
`stars_ai.standard_mod` can parse an UNEDITED.MOD-compatible database to build component
tech requirements and validate researched component availability separately from physical
slot compatibility. This separation is intentional: a component may fit a slot but still
be unavailable to the player because the required technology has not been reached.

The stock hull-row source is bundled as `stars_ai/data_hulls.mod`; it is derived from the
canonical StarsAPI/UNEDITED.MOD layout and is included in packaged builds.

## Native Core v2 (StarsAPI port foundation)

This package now includes `stars_ai.native`, a Python port/facade modeled directly on the public
`stars-4x/starsapi` structures. It parses richer Player/Race, Planet, Fleet, Waypoint, Design,
Production Queue, Battle Plan, and Object/Mystery-Trader state while preserving raw bytes for fields
that remain uncertain upstream.

Example:

```powershell
python -m stars_ai.native.cli GAME3.m1 --xy GAME3.xy --x GAME3.x1 --full --json-out GAME3.native.json
```

The intended architecture is now:

`native file crypto -> StarsAPI-style block records -> PlayerState -> normalized AI GameState -> strategy -> validated X-order writer`

Reverse engineering should target only StarsAPI TODO/gap areas (for example packet/salvage internals,
ResearchChange semantics, scores, and some order payloads) rather than re-discovering already mapped fields.

## Native GameState bridge (v2.1)

The AI decision engine can now read a native Stars! turn directly through the StarsAPI-inspired core:

```powershell
python -m stars_ai.cli play-native `
  --mfile AI.m1 `
  --xy AI.xy `
  --xfile AI.x1 `
  --player 1 `
  --out out\orders.json `
  --state-out out\gamestate.json
```

`NativeCoreTurnAdapter` converts `PlayerState` into the stable AI-facing `GameState` while preserving native details in `native` dictionaries. It also adds `.xy`-only planets as unobserved targets, associates design records to players using PLAYER design counts, and infers basic fleet roles from the player's native ship designs.

Native `.x#` writing remains a separate layer. `play-native` currently emits normalized JSON orders so the strategy engine can be validated independently before those orders are passed to the native order writer.
