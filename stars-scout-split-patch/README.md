# STARS! Singleton Scout Fleet Patch

Reviewed baseline: `Adament-Loki/stars-ai` commit `880fc7cfdf40621449fc837aeb1c9b8b98610cf5` ("Fixes for gamplay tweaking").

## What this changes

- Detects scout design slots from `state.native["design_profiles"]`.
- For every owned fleet, counts `max(0, ship_count[scout_slot] - 1)` as **wasted survey capacity**.
- Stores planner diagnostics in `state.native["scout_stack_diagnostics"]`.
- Adds a human-readable `SCOUT STACK DIAGNOSTIC` note.
- Requests `split_scout_fleet` at priority 140 for safe stacks only:
  - owned fleet
  - stationary / no active destination
  - cargo-free
  - pure scout stack
  - more than one scout in one scout design slot
- Adds an `EXPERIMENTAL` native capability for `split_scout_fleet`.
- Synthesizes one Type-24 FleetSplit + one nine-byte Type-23 MoveShips transfer for each excess scout, allocating a fresh local fleet ID each time.
- Leaves explicit TODO comments requiring regression tests and fresh Stars! client/host validation before the split codec can be considered validated.

## Apply

From the **root of your local `stars-ai` checkout**:

```bash
python /path/to/stars-scout-split-patch/apply_patch.py
```

The script creates `.pre-scout-split.bak` backups of each changed file.

Then run:

```bash
python -m compileall -q src/stars_ai
pytest -q
git diff -- src/stars_ai/fleet_management.py src/stars_ai/native_capabilities.py src/stars_ai/native/x_writer.py
```

## Safety / validation status

The planner diagnostic is straightforward and safe. The native split serializer is deliberately marked **EXPERIMENTAL**. The repository already had a real client capture showing Type-24 followed by a 9-byte Type-23 transaction, but arbitrary synthesis was disabled. This patch implements the decoded singleton transfer form and restricts it to the narrow scout use case.

Before relying on it for a long game, validate a 2-scout and 3-scout production stack through a fresh Stars! client capture and host replay. The most important validation is repeated Type-24 -> Type-23 splits from the same source fleet in one X transaction.

## Revert

Either use Git:

```bash
git restore src/stars_ai/fleet_management.py src/stars_ai/native_capabilities.py src/stars_ai/native/x_writer.py
```

or restore the `*.pre-scout-split.bak` files created by the apply script.
