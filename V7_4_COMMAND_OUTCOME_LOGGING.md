# v7.4 Command Outcome Logging

Stars! AI now persists an observable expectation for every native command that
survives writer validation. In autoplay, that pending memory transaction is
promoted only after Stars! accepts the X file and advances the game, so rejected
writer actions and failed host submissions do not become false obligations.

On each later M file, the AI classifies prior commands as `COMPLETED`, `PENDING`,
`WARNING`, or `UNVERIFIED`:

- fleet movement uses route-leg distance, warp, position, and newly observed
  planet intelligence;
- colony operations require the target to become player-owned;
- transports require arrival and the requested unload-all cargo to be gone;
- production requires the requested queue to be present or observable factories,
  mines, defenses, or design-slot ship counts to increase;
- research uses the native research setting when exposed and requested-field tech
  progress otherwise; sparse M files are explicitly `UNVERIFIED`, not falsely
  called failures;
- player relations use the actual native relation array.

Multi-waypoint routes are dependency-aware. Only the first unresolved leg can
become overdue; later legs remain pending until earlier outcomes complete. This
prevents a single missed stop from producing a cascade of redundant warnings.

Every native decision report now begins with `COMMAND OUTCOME STATUS`, including
explicit messages such as:

`WARNING - Fleet #1 should have arrived at New Hope this turn but failed to do so.`

`WARNING - Fleet #1 should have colonized New Hope this turn but failed to do so.`

The JSON decision trace includes both the outcomes checked this turn and all
pending expectations. Terminal and superseded outcomes remain in bounded memory
history for later playtest diagnosis.
