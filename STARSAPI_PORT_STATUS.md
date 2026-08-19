# StarsAPI Port Status

This branch treats `stars-4x/starsapi` as the primary native-format reference and ports proven behavior before doing new empirical reverse engineering.

## Ported now

- FileHeader / deterministic crypto (existing validated Python implementation)
- Player preamble and full-data structure
- Current six tech levels, PRT, LRT mask/names, MT mask byte order
- Race/economy raw fields that StarsAPI explicitly manipulates
- Full + partial planets: ownership, HW, visibility flags, environment/original environment, estimates, minerals, population, installations, scanner, research-leftover flag, starbase, route, observation turn
- Full + partial fleets: location, designs/counts, cargo, fuel, damage, battle plan, waypoint count, observed warp/mass
- Waypoints: target, warp, task, object type, trailing task bytes
- Designs: full/partial, transferred/starbase flags, hull, slots, armor, design turn, built/remaining counts, name
- Production queues and standard item IDs
- Battle plans: tactics, targets, attack policy, dump-cargo, names
- Objects: count, minefield, wormhole, Mystery Trader; packet/salvage preserved as known type with unknown payload
- `PlayerState.from_files()` aggregation modeled after StarsAPI `tools/PlayerState.java`

## Known upstream gaps / next targets

- Packet/salvage ObjectBlock payload internals
- ResearchChange semantic bit layout (StarsAPI class is TODO)
- PlayerScores block (StarsAPI TODO)
- SetFleetBattlePlan / RenameFleet blocks (StarsAPI TODO)
- Full task-specific waypoint payload semantics
- DesignChange creation/encoding remains incomplete upstream and empirically fragile
- Some Player fullData fields are known by role but not yet given exact UI semantics in this port
- Planet `unknownInstallationsByte` and `weirdBit` remain intentionally raw

## Agent integration rule

The strategy layer must consume `PlayerState`/normalized objects, never native byte offsets. Writers should emit only validated order types until a block writer has a round-trip and in-game acceptance test.
