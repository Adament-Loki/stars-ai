
# v5.8 Colony Lifecycle + Console Player Filtering

## Colony lifecycle fix

The v5.7 playtest exposed a strategy sequencing bug:

- At Y2400 there was no known colony target.
- The empty colony ship fell through the generic "unarmed fleet should move" rule.
- It was sent away from the homeworld.
- Later, when scouts discovered a viable target, the colony ship was no longer at
  a populated owned planet, so the validated 25 kT population load block could not be used.

v5.8 changes colony ships into protected mission assets:

1. **No known viable colony + empty + at owned planet**
   - `HOLD FOR COLONY INTEL`
   - Do not scout/reposition.

2. **Known viable colony + empty + at owned populated planet**
   - load 25 kT of population cargo (2,500 colonists) using the observed native form;
   - WaypointAdd;
   - WaypointChangeTask task 2 = Colonize.

3. **Empty colony ship away from owned population**
   - `RETURN FOR COLONISTS`
   - return to the nearest owned world before attempting the colony target.

4. **Loaded colony ship**
   - proceed to the highest-ranked known viable colony.

The colonization report keeps the ranked candidate visible even while the ship
is waiting or returning.

## Research fallback

If strategy selects an unvalidated native research field such as Propulsion, the
writer now falls back to validated Electronics rather than skipping research
every turn. The emitted record retains the requested field and marks the native
fallback.

## Selective console player logs

New JSON setting:

```json
"console_player_logs": [1, 4]
```

Behavior:

```json
"console_player_logs": null
```
Print detailed AI summaries/reports for all configured players.

```json
"console_player_logs": [2]
```
Print detailed AI summaries/reports only for Player 2.

```json
"console_player_logs": []
```
Suppress detailed per-player AI console output.

All player decision JSON/TXT files are still written to disk regardless of this
setting. Observer output remains controlled separately by:

```json
"print_observer_each_turn": true
```
