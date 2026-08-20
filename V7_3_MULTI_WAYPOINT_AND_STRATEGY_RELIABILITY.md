# v7.3 Multi-Waypoint and Strategy Reliability

This release repairs the regressions found in the v7.2 long playtest.

## Native waypoint routes

- Exploration routes now retain the fuel-safe warp selected for every leg.
- A newly assigned scout route emits sequential validated `WaypointAdd` records
  at indexes 1 through 7 instead of discarding every stop after index 1.
- Indexed `WaypointChangeTask` encoding accepts the empirically demonstrated
  waypoint index, while unsafe replacement of an existing destination remains
  blocked.
- M-file Type 19 `WaypointTask` records are parsed as waypoint slots alongside
  Type 20 `Waypoint` records, preserving fleet-to-waypoint alignment.
- Final recon deconfliction rebuilds the complete route rather than changing
  only its first destination.

## Persistent state

- Native route memory is reconstructed from the current M-file waypoint chain.
- Speculative strategy routes are rolled back before saving and only emitted
  native scan routes are recorded.
- Integrated autoplay writes per-player memory to a pending file. The pending
  state replaces committed memory only after the host advances the year and
  consumes every generated X file.

## Exploration and colonization safety

- Scout routes are limited to seven queued stops and remain within 300 ly of a
  currently owned planet.
- Idle scouts already beyond that support radius return to the nearest owned
  world before receiving another survey campaign.
- Five-year-old persistent planet intelligence becomes eligible for a refresh
  scan; colony candidates require intelligence no more than two years old.
- Colony scoring penalizes distance from owned support, rejects negative-value
  destinations, and excludes targets more than 300 ly from the empire.
- Empty colony ships at low-population owned worlds relocate to a world capable
  of supplying the validated 25 kT / 2,500-colonist population load.

The synthetic retarget/replacement path remains intentionally blocked. A native
route is either preserved when identical or extended only from a fleet without
an active destination.
