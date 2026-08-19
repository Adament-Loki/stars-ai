
# Stars! AI v4.4 — Integrated Native Autoplay

v4.4 removes the external writer requirement for the first real playtest.

Run:

```powershell
.\run-autoplay.ps1 -Config .\autoplay-config.json
```

The controller runs all four AIs, writes native `.x1`–`.x4`, invokes Stars!,
observes the generated turn, and repeats.

## Required seed files

The seed directory must contain:

- `GAME.hst`
- `GAME.xy`
- `GAME.m1`
- `GAME.m2`
- `GAME.m3`
- `GAME.m4`
- `GAME.x1`
- `GAME.x2`
- `GAME.x3`
- `GAME.x4`

The `.x#` files are **known-good X templates from the same game**. This is
necessary because Stars! X files contain static/authentication blocks that we
preserve rather than guess. The integrated writer updates the game turn,
re-encrypts the whole file, replaces supported order blocks, and preserves
the template metadata.

For initial templates, open each player turn in Stars!, make no changes, and
Save/Submit so Stars! creates a valid `.x#`. Copy those four files into the
seed directory before starting autoplay.

## Currently emitted natively

The integrated v4.4 writer emits only order forms already treated as validated:

- normal fleet movement to a planet
- planet production queues:
  - factories
  - mines
  - defenses
- SaveAndSubmit

## Deliberately not emitted yet

The AI can reason about these, but v4.4 logs/skips them until the exact native
form is validated in this integrated writer:

- Colonize waypoint task
- population load/unload
- research change
- player relations
- battle-plan assignment
- packet orders
- specialized mine tasks
- brand-new ship designs

That makes v4.4 suitable for validating the **native 50-turn loop**, but the
first competitive run will still be strategically constrained until those
remaining native actions are added.

Every player/turn writes:

`player-XX-decision-native.json`

which contains both semantic intent and which orders were emitted/skipped.
