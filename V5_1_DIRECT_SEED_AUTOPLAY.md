
# v5.1 Direct-Seed Autoplay

> Superseded by v7.2 safety behavior: `seed_dir` is now immutable. After full
> validation, the game is staged beside `stars_exe`; native hosting no longer
> operates on the seed directory.

The autoplay controller now uses the configured `seed_dir` as the **actual live
Stars! game directory**. It does not copy the game into `live/`, `turn-001/`,
`generated/`, or `submitted/` folders.

Stars! operates on one normal game set in one directory:

```
GAME.hst
GAME.xy
GAME.m1
GAME.m2
GAME.m3
GAME.m4
GAME.x1
GAME.x2
GAME.x3
GAME.x4
```

Diagnostic files are written separately under `output_dir\logs\`; no historical
game copies are made.

## Pre-host barrier

Before `stars!.exe -g GAME.hst` runs, all four agents must have completed
synchronously and the controller audits `.x1-.x4`.

For each player the audit records:
- file path
- size
- SHA-256
- parsed header
- native block inventory
- movement / production / research / submit payloads
- presence of SaveAndSubmit

If any X file fails, the host does not start.

Audit files:

```
logs\turn-001-PRE_HOST_AUDIT.txt
logs\turn-001-PRE_HOST_AUDIT.json
logs\turn-001-POST_HOST_AUDIT.txt
```

## Observer in PowerShell

After every generated turn, PowerShell prints a compact summary such as:

```
[Observer T5 Y2405] Leader P2 | P2 ... | P1 ... | no clear combat events
```

At configured checkpoints the full human-readable observer report is also
printed and written to:

```
logs\checkpoints\turn-010-OBSERVER_REPORT.txt
```

## Important

Direct-seed mode mutates the actual configured game files. Keep a separate
clean backup of your starting game outside `seed_dir`.
