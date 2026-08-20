# v7.7 native Python history merge

Headless Stars! hosting generates each player's next `.m#`, but it does not
perform the client-side operation that accumulates those observations in
`.h#`. v7.7 performs that operation natively in Python.

## Behavior

- At play-on startup, every current M is merged into its matching H. This
  repairs a sandbox left at the manual-sync boundary before any new X is made.
- After every successful host, the raw native state is snapshotted first, then
  each M is merged into H before AI strategic memory is committed.
- Planet observations are selected by their embedded observation turn. Richer
  environment data is retained when a newer sparse sighting changes ownership
  or starbase state.
- H counters, enemy player summaries, and compatible enemy ship/starbase
  design records are rebuilt. The observing player's private records remain in
  M and are not copied into H.
- The M file is never written. Pre/post SHA-256 hashes prove that it remained
  unchanged.

## Transaction and diagnostics

Each replacement H is assembled in a temporary file beside the live H, parsed,
checked for game/player identity and knowledge regressions, and atomically
installed. A post-install failure atomically restores the original bytes.

Audits are written to:

- `logs/history/<phase>-player-NN-premerge.h#`
- `logs/history/<phase>-player-NN-merged.h#`
- `logs/<phase>-HISTORY_MERGE.json`
- `logs/<phase>-HISTORY_MERGE.txt`

The subsequent HISTORY_SYNC barrier compares embedded planet observation turns
between M and H. It does not infer correctness from filesystem timestamps.

Recommended configuration:

```json
"auto_merge_history": true,
"require_history_sync": true
```
