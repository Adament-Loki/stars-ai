# Stars! AI

`stars-ai` is a Python 3.11+ AI player and native-host controller for **Stars!**. It reads either a normalized JSON state or native Stars! files, makes strategic decisions, and can run isolated multi-player native turns through the Windows host.

The current implementation is version **8.8**. Its strategic decisions, native-format confidence boundaries, and pending validation work are maintained in [AI-Status.md](AI-Status.md).

## What it does

- Generates deterministic orders for a normalized game state.
- Reads native `.m#`, `.h#`, `.x#`, and `.xy` data through a StarsAPI-inspired model.
- Plans colonization, population movement, production, research, exploration, logistics, diplomacy, and visible-threat responses.
- Maintains player memory, decision traces, command-outcome checks, and cumulative player history during automated games.
- Generates the supported native order types behind explicit safety checks, then runs a staged or play-on Windows host loop with immutable turn archives.
- Validates stock hull geometry, design legality, current six-field research, PRT/LRT component eligibility, fuel, mass, and native Type27 design bodies before a proposed ship-design order is emitted.

Native design creation is a tightly controlled live-game path; see **Native safety** below and `AI-Status.md`.

## Repository layout

```text
src/stars_ai/              Application package
  adapters/                JSON and native-state adapters
  autohost/                Native host configuration and order-file bridge contracts
  native/                  Native Stars! records, history merger, and X writer
  strategy/                Economy, exploration, military, research, diplomacy
  rules/                   Conservative rules-engine helpers
tests/                     Regression and native-format tests
examples/                  JSON turn and manifest examples
scripts/                   Maintenance helpers
run-example.ps1            Create a venv, install, and run the JSON example
run-autoplay.ps1           Run the Windows native autoplay controller
autoplay-config.example.json  Template for a native host run
```

## Source organization roadmap

The current package is functional but still has roughly sixty modules at its
top level. While a native playtest is running, files are not moved: a module
move can break an import path, a command-line entry point, or an archived
replay at exactly the wrong time. Documentation-only changes can continue
safely during that period.

After the playtest, the target layout is:

```text
src/stars_ai/
  core/                  GameState/Order contracts, agent, memory, personas
  planning/
    expansion/           colony scoring, networks, planet promotion, scouting
    economy/             production, logistics, cargo, population movement
    military/            threat assessment, doctrine, invasion, counter-design
    research/            capability catalog and research planning
    design/              design synthesis, legality, lifecycle, stock catalogs
  rules/                 Pure Stars! rules: fuel, components, population, etc.
  native/                Binary records/codecs, state reader, X writer, history
  runtime/               autoplay, Windows host, staging, playtest, archives
  observability/         decision trace, observer reports, command outcomes
  adapters/              Public JSON/native application-boundary adapters
```

This is a relocation plan, not a promise to merge unrelated behavior into a
single large file. The first consolidation slices are intentionally narrow:

- `cargo_planner`, `shared_transport`, `population_redistribution`, and
  `logistics_capacity` become the logistics planning area while retaining
  separate manifest, scheduling, and capacity responsibilities.
- `colony_planner`, `base_network`, `expansion_network`, `fleet_intent`, and
  exploration routing become the expansion planning area. Their shared
  candidate/ranking value types should be co-located, not duplicated.
- `design_*`, `ship_design_synth`, `counter_design`, stock hull/mod readers,
  and component eligibility become the design area. The native Type-27 codec
  remains in `native/` because it is a file-format concern, not design policy.
- `windows_autohost`, `host`, `playtest`, `turn_archive`, and autohost bridge
  contracts become runtime. `game_observer`, `native_observer`,
  `decision_trace`, and trace helpers become observability.

Compatibility import shims will be left in place for one release after each
move. The dated `*.pre-*.bak` files are migration evidence, not runtime code;
after their tests and binary comparisons are captured, they should move out of
`src/` into a dated archive or be removed from the repository.

### Documentation standard

New or touched Python modules receive a plain-language module docstring. Public
classes and functions state their responsibility, inputs/outputs when they are
not obvious, and any native-format or game-rule boundary. Private helpers are
documented when their name cannot fully explain a non-obvious invariant. This
work is incremental and intentionally does not alter planning behavior.

## Build and install

From PowerShell in the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install pytest
```

The package has no runtime dependencies beyond Python. `pytest` is needed only to run the test suite.

## Run the JSON example

After installation, use either the installed command or the module form:

```powershell
stars-ai play-turn `
  --state .\examples\player2-turn2405.json `
  --player 2 `
  --out .\out\player2-orders.json `
  --memory .\state\player2-memory.json
```

`run-example.ps1` performs the editable install and runs this same example.

Run all JSON players described by a manifest:

```powershell
stars-ai host-turn --manifest .\examples\game-manifest.json
```

## Inspect native files and plan one native turn

Inspect a native player turn or an existing order file:

```powershell
stars-ai inspect-stars --mfile .\sandbox\GAME.m1 --xy .\sandbox\GAME.xy --out .\out\state.json
stars-ai inspect-orders --xfile .\sandbox\GAME.x1 --xy .\sandbox\GAME.xy --out .\out\orders.json
```

Generate normalized AI orders from native state:

```powershell
stars-ai play-native `
  --mfile .\sandbox\GAME.m1 `
  --xy .\sandbox\GAME.xy `
  --xfile .\sandbox\GAME.x1 `
  --player 1 `
  --out .\out\player1-orders.json `
  --state-out .\out\player1-state.json `
  --memory .\state\player1-memory.json
```

For a lower-level native state inspection:

```powershell
python -m stars_ai.native.cli .\sandbox\GAME.m1 --xy .\sandbox\GAME.xy --x .\sandbox\GAME.x1 --full --json-out .\out\native-state.json
```

## Native autoplay

1. Copy `autoplay-config.example.json` and set `stars_exe`, `seed_dir`, `output_dir`, and the player configuration.
2. Put a disposable, matching native game in `seed_dir`: one `.hst`, one `.xy`, and a matching `.m#` / `.x#` pair for every configured AI player.
3. Keep the seed directory, live Stars! directory, output directory, and AI-memory directory separate.
4. Run:

```powershell
.\run-autoplay.ps1 -Config .\autoplay-config.json
```

Use `-Noop` to validate staging and host invocation while reusing existing X files:

```powershell
.\run-autoplay.ps1 -Config .\autoplay-config.json -Noop
```

The default controller validates the seed and live files, keeps known-good X templates, snapshots native data, merges each player's new `.m#` knowledge into `.h#`, validates that history merge, writes archive manifests and hashes, and stops on unsafe or unverified conditions. `play_on: true` continues a validated live game instead of restaging the seed.

Do not use a valuable game for initial native-order or Type27 design-creation experiments. Start with a disposable seed and retain the generated archive.

### Native autoplay JSON reference

`run-autoplay.ps1 -Config` and `python -m stars_ai.autoplay_cli --config` load the
top-level object in `autoplay-config.json` directly into the native autoplay
configuration. Unknown fields are rejected; omitted fields use the defaults in
the application. Start from [autoplay-config.example.json](autoplay-config.example.json).

The controller always runs the live game beside `stars_exe`; there is no
`live_dir` setting. `seed_dir` is the immutable source when starting a new run,
and `output_dir` holds reports, logs, archives, and copied native snapshots.

| Field | Type / default | How it is used |
| --- | --- | --- |
| `stars_exe` | required path | The Stars! executable. The controller launches it with `-g` and uses its directory for the live game files. |
| `seed_dir` | required path | Immutable, complete starting game. It must contain one matching `.hst` and `.xy`, plus `.m#`, `.h#`, and `.x#` files for every AI player. |
| `output_dir` | required path | Destination for run logs, observer reports, archived snapshots, generated order copies, and `autoplay-result.json`. |
| `game_name` | optional string; ignored for selection | Compatibility field only. The actual game basename is discovered and validated from `seed_dir`, then replaces this value. |
| `player_ids` | integer array; `[1,2,3,4]` | Player seats controlled by the AI. Each listed player needs matching native files and receives an order file each turn. |
| `turns` | integer; `50` | Number of host generations to attempt. |
| `play_on` | boolean; `false` | `false` stages a fresh live game from the seed. `true` validates and continues the existing live game for `turns` additional generations. |
| `checkpoints` | integer array; `[10,25,50]` | Turns that receive full observer reports under `logs/checkpoints/`; the latest is copied to `LATEST_OBSERVER_REPORT.txt`. |
| `host_password` | string or `null`; `null` | When supplied, adds the password to the Stars! host invocation. Keep real passwords out of committed JSON. |
| `keep_every_turn` | boolean; `true` | Saves a post-host native snapshot for every completed turn under `logs/native/`. |
| `auto_merge_history` | boolean; `true` | Merges each current player `.m#` into its cumulative `.h#` during bootstrap and after successful hosting. |
| `require_history_sync` | boolean; `true` | Refuses to create/submit subsequent orders unless semantic M-to-H history coverage validates. Keep enabled for normal games. |
| `stop_on_missing_x` | boolean; `true` | Stops before hosting if any configured player is missing its newly generated `.x#` order file. |
| `host_timeout_seconds` | integer; `180` | Maximum time for Stars! to launch, finish, and produce settled output files before a diagnostic timeout is written. |
| `host_poll_seconds` | number; `0.5` | Poll interval while waiting for the host process or generated files. |
| `host_settle_seconds` | number; `1.5` | Required quiet period after native file changes before they are treated as complete. |
| `prevent_parallel_stars` | boolean; `true` | Refuses to launch while another matching Stars! process is running, and waits for the launched process to exit. |
| `use_seed_as_live` | deprecated boolean; `false` | Retained for old configuration compatibility only. It does not select the live directory; the controller always uses the directory beside `stars_exe`. |
| `pre_host_audit` | boolean; `true` | Reads every generated `.x#` before launch and blocks hosting if its header, player, game identity, or order blocks are invalid. |
| `print_observer_each_turn` | boolean; `true` | Prints a concise omniscient observer status after every completed host turn. Full chronological output is always written to `RUNNING_GAME_REPORT.md`. |
| `cleanup_output_on_start` | boolean; `true` | Removes prior run output at `output_dir` before staging. Persistent AI memory and X templates are preserved because they default outside that directory. |
| `console_player_logs` | integer array or `null`; `null` | Selects detailed AI decision logs to echo: `null` means all configured AI players, `[1,2]` limits detail to those players, and `[]` suppresses player detail and prints the observer’s full per-turn section instead. |
| `allied_pairs` | two-integer arrays; `[]` | Reciprocal Friend relationships supplied to AI strategy, for example `[[1,2]]` for P1 and P2. They affect AI planning; they do not serialize diplomacy orders. |
| `personas` | object of player-ID strings to names | Assigns the strategy persona for each AI player and labels it in decision/observer reports, for example `{"1":"Balanced","2":"Expansionist"}`. |
| `x_template_dir` | path or `null`; `null` | Permanent known-good `.x#` templates. `null` creates a sibling directory beside `seed_dir`, safely outside both the seed and disposable output directory. |
| `ai_state_dir` | path or `null`; `null` | Persistent per-player strategic memory. `null` creates a sibling directory beside `seed_dir`, so learned state survives output cleanup and play-on runs. |
| `turn_archive_enabled` | boolean; `true` | Captures immutable native-state phases before writing, before hosting, after the host attempt, and after commit/failure. |
| `turn_archive_include_logs` | boolean; `true` | Includes relevant generated logs in each turn-archive phase. |
| `turn_archive_json_index` | boolean; `true` | Maintains `logs/turn-archive/index.json` and each turn’s `turn.json` index for programmatic archive lookup. |

`playtest-config.json` is a separate, lightweight configuration for the
in-process playtest harness, not the Windows native host loop. Its fields are:

| Field | How it is used |
| --- | --- |
| `game_name` | Label for the playtest and its output directory. |
| `max_turns` | Maximum simulated turns. |
| `checkpoints` | Simulated turns that save recap JSON. |
| `seed` | Deterministic seed for repeatable simulated games. |
| `players` | Array of player objects used to construct the simulation. |
| `players[].player_id` | Numeric player seat. |
| `players[].label` | Human-readable report label. |
| `players[].persona` | Strategy persona assigned to that player. |
| `players[].prt` | Primary racial trait used by the simulation. |
| `players[].human` | When `true`, reserves the seat as human-controlled instead of AI-controlled. |
| `turn_archive_enabled` | Enables archive capture for the playtest. |
| `turn_archive_include_logs` | Includes playtest logs in archive captures. |
| `turn_archive_json_index` | Writes JSON indexes for archive captures. |

## Test

Activate the virtual environment, then run:

```powershell
$env:PYTHONPATH = "src"
python -m pytest -q
```

Run a focused test while working on a subsystem, for example:

```powershell
python -m pytest -q tests\test_v88_ship_creation.py
python -m pytest -q tests\test_turn_archive_v871.py
```

Tests are grouped by the behavior they protect: native parsing and X serialization, history merging, colonization and logistics, research, ship design, strategic planning, autoplay staging, and archive integrity.

## Native safety

The project favors a fail-closed order writer over speculative serialization:

- Native parsing preserves raw data where a field is not yet fully understood.
- Fleet moves, supported waypoint tasks, colonization, production, research, and submission are emitted only in validated forms; unsupported mutations are skipped and reported.
- Existing X files are treated as controlled templates rather than arbitrary bytes to overwrite.
- Ship designs use the canonical stock-hull model plus the bundled StarsAPI `UNEDITED.MOD`; every component is checked against current research and official PRT/LRT gates before it can be encoded. Generic ship proposals are compiled through that same gate into the Type27 lifecycle; unvalidated starbase mutations remain advisory.
- A design replacement is always delete, read back on a later turn, then create. It is never an atomic same-turn mutation.
- Type27 creation emits only the captured owner-aware staging/final pair (`01 A4` for Player 1; `11 A4` for Player 2), without unrelated orders or Type46. The Player-1 Fuel-Mizer replay is host-accepted; Player 2 and deletion remain separately gated pending dedicated host replays.

See `AI-Status.md` for the exact confidence boundary and the recommended next validation sequence.

## Development notes

The main command entry point is `src/stars_ai/cli.py`; the native autoplay entry point is `src/stars_ai/autoplay_cli.py`. Strategy code consumes the stable `GameState` model rather than native records directly. The native adapter and writer form the boundary between strategy and Stars! binary files.

Generated files belong under `out/`, `playtests/`, `sandbox/`, or a separately configured game/output location. Avoid modifying a live game before the configuration and seed validation steps have completed.
