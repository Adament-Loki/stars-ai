
# v5.3 Fleet Activity + Playtest Cleanup

## Automatic cleanup

`cleanup_output_on_start` defaults to `true`.

At the start of a run the configured `output_dir` is deleted and recreated,
removing old audit, observer and decision logs.

Safety:
- `seed_dir` is never deleted.
- cleanup refuses to run if `seed_dir == output_dir`.
- cleanup also refuses to run if `seed_dir` is nested inside `output_dir`.

If you want the whole `playtests` directory cleaned each run, set `output_dir`
to that directory and keep the live Stars! seed/game elsewhere.

## Scout targeting

The previous exploration score could reward frontier distance more than travel
distance, causing cross-galaxy moves.

v5.3 uses local-first expanding-frontier exploration:
1. start with unknown planets near the fleet's actual `(x,y)`;
2. widen the radius only when local targets are exhausted;
3. use frontier distance only as a modest tie-breaker.

## Why only one fleet was moving

The native state often contains several starting fleet roles, but current safe
native capability limits their actions:

- Scout: can use validated simple movement.
- Colony: strategic colonize order is currently skipped until native Colonize
  task encoding is validated.
- Freighter: logistics is currently blocked because cargo capacity is not yet
  reconstructed in the adapter.
- Combat: normally waits for a military target.

v5.3 allows idle `combat` or `unknown` fleets to perform SHORT-RANGE auxiliary
reconnaissance during the first ten years when no enemy territory is known.
Colony and freighter fleets remain reserved.

## Diagnostics

Every decision JSON now contains one `FLEET STATE:` line per owned fleet,
including role, current coordinates, destination and speed.

PowerShell also prints each player's successfully emitted movements, e.g.:

```
[AI P1 Y2400] emitted moves: F0->P12, F3->P18; skipped moves: 1
```

This lets us distinguish planning inactivity from native-writer skips without
opening the JSON after every turn.
