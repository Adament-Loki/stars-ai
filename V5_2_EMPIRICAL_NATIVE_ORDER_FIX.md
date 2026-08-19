
# v5.2 Empirical Native Order Fix

A controlled four-player Stars! sample exposed two native serialization bugs.

## Fleet owner bits

Real WaypointAdd fleet fields:

```
P1 fleet 0 = 00 00
P2 fleet 0 = 00 02
P2 fleet 4 = 04 02
P3 fleet 0 = 00 04
P4 fleet 0 = 00 06
```

The writer now uses:

```
((player_id - 1) << 9) | local_fleet_id
```

## FileHash order-stream length

The first uint16 in the real type-9 FileHash block changes with submitted orders:

```
1 WaypointAdd + Submit: 14 + 6 = 20 decimal = 14 00
2 WaypointAdd + Submit: 14 + 14 + 6 = 34 decimal = 22 00
1 ResearchChange + Submit: 4 + 6 = 10 decimal = 0A 00
```

The previous writer copied those two bytes unchanged from the template even after changing orders. v5.2 recomputes them for every generated X file.

## Target flags

Real files use both `11` and `91` as the final WaypointAdd byte. StarsAPI labels the upper nibble as unknown, while the low nibble `1` identifies a planet. v5.2 keeps the proven `11` form rather than guessing when `91` is required.

## Test sequence

Run a single turn from a clean copy first. Check the pre-host audit and verify that fleets actually move before increasing the run length.
