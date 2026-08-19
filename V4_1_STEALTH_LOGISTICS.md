
# v4.1 Stealth & Refueling Logistics

Routing now recognizes two Stars! operational benefits of planets:

1. Fleets arriving at an owned planet with a starbase are treated as instantly
   refueled for route planning.
2. Planetary orbit is treated as concealment against enemies that lack
   penetrating scanners.

This changes route selection from shortest-path-only to a multi-objective route:

- arrival time
- fuel endurance
- instant starbase refueling
- gate access
- planetary concealment
- probability the enemy has penetrating scanners
- strategic risk

A strike fleet may therefore deliberately travel:

Home -> Relay Starbase -> Target

even when a direct route is shorter, because Relay resets fuel and reduces the
fleet's exposure before the final approach.

Planetary concealment is not assumed absolute. Intelligence estimates a
penetrating-scanner probability, and the concealment value decreases as that
probability rises.

The logic is intended to support later operational tactics such as:
- hidden staging fleets
- starbase-to-starbase reinforcement corridors
- concealed pre-positioning near a frontier
- deceptive routing
- fuel-safe raiding routes
