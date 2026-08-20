
# Stars! AI v4.3 — Automated 4-AI / 50-Turn Host Runner

## Goal

Automate this complete native Stars! loop:

1. Read each player's `.m#` + shared `.xy`.
2. Run that player's isolated AI.
3. Produce a valid native `.x#`.
4. Submit all four `.x#` files to the host sandbox.
5. Execute:
   `stars!.exe -g gamename.hst`
6. Read the resulting next-turn `.m#` files.
7. Let the omniscient observer read `.hst`.
8. Save checkpoints at turns 10, 25 and 50.
9. Repeat through turn 50.

## Critical boundary

The host automation is complete.

The remaining integration gate is the native semantic-order writer. The current
native GameState adapter deliberately writes JSON decisions rather than pretending
they are `.x#` files. v4.3 therefore requires a native writer bridge and will
stop rather than submitting invalid data.

This is intentional. A fake `.x#` would corrupt the playtest and make every
later strategic conclusion suspect.

## Windows host command

Stars!' documented forced-host command is:

`stars!.exe -g gamename.hst`

It generates the next turn regardless of whether all players submitted and then
exits. Our runner nevertheless requires all four `.x#` files by default.

## Seed directory

Place a disposable four-human-slot game in one directory:

- `AIPLAY.hst`
- `AIPLAY.xy`
- `AIPLAY.m1`
- `AIPLAY.m2`
- `AIPLAY.m3`
- `AIPLAY.m4`
- `AIPLAY.x1`
- `AIPLAY.x2`
- `AIPLAY.x3`
- `AIPLAY.x4`

The slots should correspond to the four AI races/personas you want tested.

`seed_dir` is the immutable source of the complete initial game. The basename
is discovered from these files; `game_name` is not required. Before any live
file changes, all required M/X pairs are parsed and checked for matching game,
turn, player registration, FileHash length, and X lifecycle structure. The
validated game is then staged into `Path(stars_exe).parent`, where all native
reads, writes, observer work, and hosting occur. Only stale files for that exact
game basename are removed from the executable directory.

### Midgame play-on mode

The default remains a clean seed reset. To continue the current validated game
beside `stars_exe` instead, set:

```json
"play_on": true,
"auto_merge_history": true,
"require_history_sync": true,
"turns": 10
```

This means “play 10 additional turns from the current live year.” In play-on
mode the runner validates the live HST, XY, and every configured player M-file
against the seed game id, player seats, and common current turn before changing
anything. It does not stage the seed or remove/replace the current sandbox game
at startup. The pre-run bootstrap directory is a snapshot of that midgame state.

Live `.x#` files are not required because Stars! normally consumes them during
hosting. The runner reuses matching persistent templates or bootstraps them from
the seed’s validated X files. Set `play_on` back to `false` (or omit it) for the
normal clean reset from `seed_dir`.

With `auto_merge_history` enabled (the default), the runner natively merges each
newly generated `.m#` into the matching cumulative `.h#`; opening each player
turn in Stars! is no longer required. It preserves pre/post H copies and hashes
under `logs/history/`, verifies that the current M was not changed, and checks
embedded per-planet observation turns before another `.x#` is written.
`require_history_sync` keeps that semantic verification fail-closed. Leave both
settings enabled for editor-safe playtests.

When `keep_every_turn` is enabled, every completed host attempt is frozen below
`logs/native/turn-NNN-post-host/`. Each directory includes all native game
files (including the pre-merge `.h#`) plus a manifest of sizes, hashes, and
parsed headers. The matching merged histories are in `logs/history/`.

`output_dir/bootstrap/manifest.json` records the source/execution paths,
configured players, and SHA-256 for each initial X file. Keep `seed_dir`, the
Stars! executable directory, `output_dir`, and `ai_state_dir` separate.

Do not password-protect the host file for this automation.

## Output

Every generated turn gets an immutable debug directory:

`turns/turn-001/submitted/`
`turns/turn-001/generated/`
`turns/turn-001/host-command.json`
`turns/turn-001/host.stdout.txt`
`turns/turn-001/host.stderr.txt`
`turns/turn-001/execution.json`

Checkpoints additionally create:

`checkpoints/turn-010/`
`checkpoints/turn-025/`
`checkpoints/turn-050/`

Each checkpoint contains `.hst`, `.xy`, all four `.m#` files, and an
`observer-manifest.json`.

## Native writer command contract

The runner can invoke an external native writer for each player.

Available placeholders:

- `{player_id}`
- `{m}`
- `{xy}`
- `{existing_x}`
- `{output_x}`
- `{turn_dir}`

Example:

`python native_writer.py --player {player_id} --m "{m}" --xy "{xy}" --out "{output_x}"`

The command MUST create a valid native Stars! `.x#` at `{output_x}` and return 0.

## Run

PowerShell:

`.\run-autoplay.ps1 -Config .\autoplay-config.json -WriterCommand 'python native_writer.py --player {player_id} --m "{m}" --xy "{xy}" --out "{output_x}"'`

## Host-loop diagnostic

`.\run-autoplay.ps1 -Config .\autoplay-config.json -Noop`

Noop mode only reuses already-existing native `.x#` files and is not AI play.
It exists to test the Windows Stars! host invocation and snapshot behavior.

## Failure behavior

The runner stops on the first failed generation and preserves the exact submitted
and generated files. Resume/replay can therefore start from the previous snapshot
without destroying evidence.

The default host timeout is 180 seconds and remains configurable with
`host_timeout_seconds`. Timeout diagnostics record file activity and detectable
Stars! process state to distinguish slow hosting from a likely modal/error state.


## v4.4
The integrated native writer is now the default. No `-WriterCommand` is required. Initial `.x1`–`.x4` templates from the same game are required in the seed directory.
