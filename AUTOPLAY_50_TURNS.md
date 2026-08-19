
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

The slots should correspond to the four AI races/personas you want tested.

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


## v4.4
The integrated native writer is now the default. No `-WriterCommand` is required. Initial `.x1`–`.x4` templates from the same game are required in the seed directory.
