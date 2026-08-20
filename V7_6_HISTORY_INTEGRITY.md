# v7.6 native history integrity

Stars! keeps a player's cumulative knowledge in `GAME.h#`, but the host does
not merge `GAME.m#` into that history. The Stars! client performs that merge
when the player opens the M file. Submitting another X before that happens can
make a transient planet discovery disappear from later editor turns.

The v7.6 manual workflow has been superseded by the v7.7 native Python merger.
The autoplay runner now protects that boundary with these mechanisms:

- `"auto_merge_history": true` is the safe default. The runner merges the
  current M data into H automatically at play-on startup and after every
  successful host generation.
- `"require_history_sync": true` verifies actual embedded planet observation
  turns rather than file modification times. A failure produces structured
  HISTORY_MERGE/HISTORY_SYNC diagnostics and stops before another X is made.
- `"keep_every_turn": true` now works. Immediately after hosting, the runner
  copies every native file for the game into
  `logs/native/turn-NNN-post-host/` and records SHA-256 hashes and native header
  metadata in `manifest.json`.

## Safe unattended workflow

1. Run from the seed or set `"play_on": true` to continue the live sandbox.
2. The runner snapshots the raw native files, merges every M into H, validates
   cumulative knowledge, and continues for the configured number of turns.
3. Inspect `logs/history/*-HISTORY_MERGE.*` if an automatic merge fails.

No client opening or other per-turn manual intervention is required.

Scan outcome logging was tightened at the same boundary: a scout arriving at a
planet is only marked complete after new planet intelligence is observed.
Arrival without an observation is reported as an explicit warning.
