
# v5.9 Purposeful Fleet Invariants

The prior package accidentally retained the old rule that an unarmed fleet
should receive some movement simply to avoid being idle. That is removed.

## New invariant

Every fleet needs a PURPOSE, not necessarily a MOVE.

### Colony ships
- Never scout.
- Never move to an unknown or unverified planet just to stay active.
- If no known viable colony exists: hold at an owned planet.
- If a viable colony exists and the ship is at population: emit the complete
  validated 25 kT / 2,500-colonist load + WaypointAdd + Colonize task=2 sequence.
- If empty and away from population: return to the nearest owned planet first.

### Freighters / cargo ships
- Never reposition merely because idle.
- Move only for a selected cargo source/destination/transfer mission.
- Otherwise: HOLD FOR LOGISTICS.

### Remote miners
- Never move to an unknown planet merely because idle.
- Reposition only to an observed, unowned planet with mineral concentration data.
- Otherwise: HOLD FOR MINING TARGET.

### Minelayers
- Hold until a validated strategic mine-laying mission exists.

### Scouts / recon
- These are the roles that may intentionally move to unknown worlds for information.

Console player filtering from v5.8 remains supported.

Research requests whose native encoding is not validated are now emitted as
validated Electronics with the requested strategic field retained in diagnostics,
rather than being skipped every turn.
