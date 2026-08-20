# STARS! AI v8.7.1 — immutable turn archives

This overlay adds evidence-grade per-turn snapshots to the Windows autoplay loop. It does **not** change Type27 design bytes.

For every autoplay turn (default enabled), the runner writes under:

`<output_dir>/logs/turn-archive/turn-###/`

Phases:

- `00-pre-write/` — exact live native game before any prior X is removed or AI X is generated.
- `10-pre-host/` — exact game after all AI X files are generated, before the pre-host audit / Stars! launch.
- `20-post-host-attempt/` — game immediately after the host returns and post-host registration checks complete.
- `30-committed/` — successful next-turn state after history merge and AI memory commit.
- `99-failure/` — best-effort snapshot from the outer exception handler, even when hosting/auditing aborts before normal post-host snapshot code.

Each phase contains:

- `game/` — `.hst`, `.xy`, `.m#`, `.h#`, and any `.x#` present at that exact phase.
- `x-templates/` — immutable writer templates used by the run.
- `ai-state/` — committed and pending strategic-memory JSON when present.
- `logs/` — this turn's generated X copies, decision JSON/reports, audits, host logs, history logs, observer/checkpoint files available at capture time.
- `manifest.json` — SHA-256, size, mtime, parsed native header/block inventory, source-stability check, redacted config, and phase metadata.

Archives are immutable: an existing phase directory is never overwritten. A repeated capture becomes `phase-02`, etc.

## Install

Extract over the repo root, then:

```powershell
python .\APPLY_TURN_ARCHIVE_PATCH.py
pytest -q tests\test_turn_archive_v871.py
```

The patcher is intentionally guarded to public-main `src/stars_ai/windows_autohost.py` Git blob `78b201eee791b792f17dd425b77488ac694055ef`. v8.7 did not intentionally modify this file. Do not bypass a mismatch; rebase the patch if your local autoplay runner differs.

## Verify an archived phase

```powershell
python .\VERIFY_TURN_ARCHIVE.py .\playtest-output\logs\turn-archive\turn-003\10-pre-host
```

## Prepare a disposable replay/evidence copy

```powershell
python .\PREPARE_TURN_REPLAY.py `
  .\playtest-output\logs\turn-archive\turn-003\10-pre-host `
  .\replays\turn-003-pre-host
```

This only copies the archive; it never modifies the live game or the immutable source archive.

## Why this is needed for the current failure

The supplied logs already preserve successful post-host snapshots for Turns 1 and 2, but Turn 3 stops after a READY pre-host audit. There is no Turn-3 post-host audit/snapshot. The supplied live `GAME.x1` is byte-identical to `turn-003-player-01-GENERATED.x1` (SHA-256 `1f22a553a27fc3c162de03f8e644d3375b10bf387dd38d157d9af6d81fdb02c8`). v8.7.1 closes that failure-evidence gap.
