# Stars! AI architecture

## Repository map

```text
src/stars_ai/
  ai/             Turn facade, typed AI state, planner pipeline, state store
  autohost/       Hosted-game configuration, bridge contracts, lifecycle
  native/         Stars! adapters/codecs, X writer, package native resources
  strategy/       Planner-domain entry functions retained for compatibility
  rules/          Rules-engine helpers
  *.py            Established domain modules not yet safe to relocate wholesale

tests/
  TestResource/   Immutable test inputs and golden references
  helpers/        Test-only resource resolution/copy utilities
  test_*.py       Automated behavior and integration checks

playtests/
  seed/           Native seed games used by runtime/playtest workflows
  runs/           Checked-in forensic playtest evidence
```

## Turn execution boundaries

`StarsAI.run_turn()` is the semantic AI entry point. It uses typed `AIState`,
the planner pipeline, and `AIStateStore`-compatible persistent memory. The
legacy `StarsAgent` remains a compatibility wrapper.

The native writer receives a semantic `OrderSet`; it validates supported
capabilities, serializes native commands, and returns emitted/skipped audit
facts. It does not select strategy or persist memory.

The autohost application layer coordinates decoded observations, `StarsAI`,
native writing, pre-host auditing, Stars! hosting, history verification, state
commit/rollback, reports, and immutable turn archives. Pending memory commits
only after the full host lifecycle succeeds.

## Reporting and evidence

Decision-journal reporting renders concise player reports at the run root and
verbose per-player diagnostics below it. `turn_archive` records immutable
pre-write, pre-host, post-host, committed, failure, and approved-action
evidence as applicable.

## Resources

Runtime MOD databases are package resources under
`src/stars_ai/native/resources/mod/` and are located through
`native_resource_path()`, never from the current working directory. Runtime
X templates are game-specific: autohost derives them from a validated seed and
keeps mutable copies beside that seed. The package intentionally ships no
generic game X templates.

`tests/TestResource/` contains immutable test-only sandboxes, autoplay
scenarios, AI-state snapshots, captured X templates, and regression fixtures.
Tests use `tests.helpers.resources.test_resource()` and copy mutable inputs to
`tmp_path`; production code never imports from `tests`.

The established top-level domain modules remain in place deliberately. They
have wide import surfaces, and Phase 9 avoids a cosmetic mass move that would
make behavior-preservation review harder. Future domain extraction must retain
compatibility imports and a regression comparison.
