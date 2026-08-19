
# v5.7 Colony Cargo Diagnostics + Mission Warp Fix

## Root cause: slow fleets

v5.6 incorrectly treated the fleet's currently observed waypoint warp as if it
were the fleet's movement capability. Every later planner used `min(fleet.speed,
X)`, so a fleet previously moving at warp 2 or 3 could remain permanently capped
at that speed.

v5.7 separates:
- `native.observed_warp`: what the current/previous order used;
- planning speed: normal mission-speed baseline.

Mission policy currently uses:
- scouts: warp 8
- remote miners/minelayers: warp 7 for short legs, warp 8 for longer travel
- colony ships: warp 7 short / warp 8 long
- transports: warp 7
- combat/utility: warp 8

Warp 9 is deliberately avoided until engine safe-warp capability is decoded.

## Colony-load diagnosis

v5.6 only emitted the 25k population load when the colony fleet could be mapped
to an owned source planet. v5.7 makes source detection robust:
1. use `position_object_id` when valid;
2. fall back to exact planet/fleet coordinates.

Every decision report now states:
- population aboard before the order;
- source planet population;
- whether the native 25k load block was emitted;
- destination;
- selected warp.

This lets the next run distinguish:
- planner failed to request load;
- writer failed to emit load;
- Stars! rejected/ignored a correctly emitted load block.
