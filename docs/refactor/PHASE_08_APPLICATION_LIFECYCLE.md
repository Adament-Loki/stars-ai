# Phase 8 application lifecycle

`windows_autohost.run_50_turn_game` is the compatibility entry point for a
hosted game.  It retains the established seed, history, host, observer, and
archive operations, while `autohost.lifecycle` makes the turn boundary
explicit:

```text
validate / stage game
  -> observe current M state
  -> archive pre-write evidence
  -> clear abandoned pending memory
  -> create semantic AI orders and serialize native X files through bridge
  -> archive pre-host evidence and audit it
  -> host Stars!
  -> verify host and history outcome
  -> commit staged memory on complete success, otherwise roll it back
  -> report observer outcome and archive committed evidence
```

`HostedTurnLifecycle` owns only the transaction timing.  It does not plan AI
strategy, serialize native orders, execute Stars!, write reports, or copy
archive files.  `TurnArchiver` binds the immutable evidence context once per
run and delegates every capture to the existing `turn_archive` functions.

The ordering intentionally preserves a `00-pre-write` archive before stale
pending memory is discarded.  A native-writer, audit, host, or history failure
cannot promote pending AI memory; only the complete successful host path calls
the bridge's `commit_pending_memory()` hook.
