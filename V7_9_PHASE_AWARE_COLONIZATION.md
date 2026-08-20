# v7.9 phase-aware colonization

Colonization now evaluates the planet's race-adjusted habitability before
issuing orders or building additional colony ships.

- Turns 0-15 normally require at least 60% habitability. A 50-59% world is an
  exception only when its mineral concentrations are exceptional or it is a
  compact bridge to a large local frontier.
- Turns 16-25 broaden the normal floor to 50%.
- Turns 26-40 broaden it to 35% and give minerals more weight.
- Turns 41-55 use a 20% normal floor and actively value resource outposts.
- After turn 55, any green world can be considered and mineral quality has a
  major effect on rank.
- Universal-habitability races remain resource-driven at every phase.

Habitability is already derived from the active race's environmental ranges
and immunities. Fuel safety, source-population reserves, support distance, and
target-intel freshness remain hard constraints, so a good planet does not
override an infeasible short-term operation.

Decision payloads and native reports identify the phase, racial habitability,
normal floor, and whether selection was based on habitability, exceptional
resources, or frontier value.
