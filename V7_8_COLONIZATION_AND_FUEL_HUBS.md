# v7.8 colonization and fuel hubs

v7.8 fixes the opening expansion deadlock found in the five-turn
`latestdemo` replay and turns starbase-network advice into native production
orders.

## Colonization

- The first opening colony may launch once its source reaches 3,500
  colonists: the validated native load moves 25 kT of population cargo,
  which is 2,500 colonists, and retains at least 1,000.
- After the first new colony or turn 10, the normal 50,000-colonist source
  reserve returns.
- Empty colony ships stage at the best population/refuel world when a viable
  target exists but cannot yet be launched safely.
- Candidate reachability and final warp selection include the 25 kT population
  cargo that the same native order will load.

## Fuel-hub starbases

- Orbital Forts are evaluated as non-refueling, non-shipyard infrastructure.
- A remote fort with a network gap or nearby frontier becomes a `FUEL_HUB`
  candidate.
- At most one hub project is active per empire. The AI reuses the lightest
  existing operational starbase design, avoiding unsafe design creation.
- Native custom production IDs `0..15` are ship designs and `16..25` are
  starbase designs. A starbase slot is emitted as `16 + design_slot`.
- In-progress starbase completion percentages are preserved when the queue is
  reissued, and command-outcome logging verifies either the queued design or
  the completed planet upgrade on the following turn.

## Replay result

In the v7.8 replay of the final `latestdemo` state, both AIs emitted a complete native
25 kT / 2,500-colonist load-and-colonize operation for their viable target,
and each queues its existing Space Station design at its remote Orbital Fort.

v7.9 supersedes only that replay's target eligibility: its selective opening
policy now holds for sub-60% ordinary worlds and chooses known 60%+ alternatives.

For a 25,000-colonist source, the decision payload and report therefore show
22,500 colonists remaining after the load.
