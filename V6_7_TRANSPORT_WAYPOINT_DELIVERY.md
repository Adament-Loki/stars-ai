
# v6.7 Complete Transport Waypoint Delivery

Controlled Stars!-generated sample:
- Fleet: Swashbucker 4
- waypoint task: Transport
- Ironium: Unload All
- Boranium: Unload All
- Germanium: Unload All
- Colonists: Unload All
- Fuel: Load Optimal

The Type 5 WaypointChangeTask contains this exact additional payload:

```text
00 20   Ironium    Unload All
00 20   Boranium   Unload All
00 20   Germanium  Unload All
00 20   Population Unload All
00 70   Fuel        Load Optimal
```

Stars! safely ignores an Unload All instruction for a cargo category that is
empty, so population can always be included.

## New normal logistics transaction

1. Load source cargo using a validated load form.
2. Set the destination waypoint.
3. Set that waypoint's task to Transport.
4. Let Stars! execute:
   - Unload All Ironium
   - Unload All Boranium
   - Unload All Germanium
   - Unload All Population
   - Load Optimal Fuel

The previous planned two-turn partial unload workaround is removed from normal
operation.

If a freighter is unexpectedly observed at an owned planet with cargo still
aboard, the recovery path reissues this same fully validated policy locally
before assigning another route.

## Additional evidence

The same controlled X file contains a manual-load block for exact 20/20/20
Ironium/Boranium/Germanium:

```text
03 00 25 00 12 07 14 14 14
```

v6.7 records this as supporting evidence but keeps the autonomous source load at
the already validated 10/20/30 form rather than generalizing arbitrary values.
