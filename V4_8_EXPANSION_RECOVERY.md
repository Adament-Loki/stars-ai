
# v4.8 Expansion Recovery

The first 50-turn native autoplay exposed structural passivity.

## Fixed
- Completed scout waypoints no longer leave scouts permanently marked "busy".
- Exploration target scoring strongly rewards extending the frontier.
- Observed planets receive a conservative temporary habitability estimate based on environmental distance from the player's homeworld.
- Early empires lower colonization selectivity until they establish several worlds.
- Colony fleet orders are now emitted with Stars! waypoint task 2 (Colonize).
- Early-game traces include an expansion watchdog if scouts exist but no movement is generated.

## Still unresolved
Native `ResearchChange` serialization is not yet integrated. The strategy engine may choose a research field, but Stars! will continue using the seed game's current field until that block format is validated and written.

## What to test
Do a fresh 10-15 turn run first. By turn 10 we should see:
- scouts repeatedly receiving new destinations,
- exploration radius growing,
- colony fleets moving toward observed suitable worlds,
- at least some empires owning more than one planet.

Inspect `player-XX-decision-native.json` to compare semantic movement against native emitted movement.
