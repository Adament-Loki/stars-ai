
# v6.2 Multi-turn Runtime Fix

This release addresses three independent failures visible after Turn 1.

## 1. Live X files are disposable, templates are not

Stars! may consume/delete/move `GAME.x1`, `GAME.x2`, etc. after host generation.
The AI no longer expects those live files to survive.

On the first run only, manually-created Turn-0 X files bootstrap immutable
per-player templates. By default they are stored in a sibling directory:

`<seed-folder>-stars-ai-x-templates/`

That location is outside:
- the Stars! live game directory; and
- `output_dir`, which can be deleted by `cleanup_output_on_start`.

Every turn, including after a process restart, the AI:
1. reads the current `.m#`;
2. loads the persistent player X template;
3. synchronizes game/turn header data;
4. builds a completely fresh `.x#` in the live game directory;
5. hosts the turn.

Optional JSON override:

```json
"x_template_dir": "C:\\StarsAI\\templates\\GAME"
```

After the first successful bootstrap, live `.x#` files do not need to exist when
the tool starts.

## 2. Second and later fleet destinations

The old writer always emitted Type 4 `WaypointAdd` for a new movement decision.
That works for a fleet with only waypoint #0, but after the first trip Stars!
can retain waypoint #1.

StarsAPI's state logic treats:
- Type 4 WaypointAdd as **insert**;
- Type 5 WaypointChangeTask as **replace**.

v6.2 therefore uses:
- `waypoint_count < 2` -> ADD waypoint #1;
- `waypoint_count >= 2` -> CHANGE waypoint #1 with task 0.

Colonize and Transport use the same route lifecycle before applying task 2 or 1.
The decision report now prints `NATIVE ADD WAYPOINT` or `NATIVE CHANGE WAYPOINT`
and the M-file waypoint count.

## 3. Population units

Native Stars! surface and fleet-cargo population use different representations.
The adapter previously passed raw values directly into an AI model whose
thresholds use individual colonists.

v6.2 normalizes:
- planet population was initially believed to be raw * 1000 (corrected to
  raw * 100 in v6.6);
- fleet population cargo was initially believed to be raw * 1000 (corrected
  to raw kT * 100 colonists in v7.8).

A native fleet cargo value of 25 means 25 kT, or 2,500 colonists.
