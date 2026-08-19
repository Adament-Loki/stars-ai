
# Stars! AI v6.8 — Race-Aware Strategy, Dynamic Cargo, Design Development

## Release status

Full regression suite: 214 passed.

## 1. Race-aware colonization

The native race block is now used directly for habitability.

Full race data is decoded as:
- bytes 8..10: habitat centers
- bytes 11..13: habitat lows
- bytes 14..16: habitat highs

A tri-immune race encoded as `FF FF FF / FF FF FF / FF FF FF` is treated as
universal-habitable.

For universal-hab races:
- habitability is removed from colony ranking
- mineral concentrations are heavily weighted
- travel distance matters
- frontier-opening value matters
- nearby expansion-cluster value matters

This is intended for races such as the Player 2 test race that grows the same
on all planetary environments.

## 2. Player 1 post–Fuel Mizer research doctrine

When:
- the race has Improved Fuel Efficiency,
- Propulsion is at least 2 or a Fuel Mizer design already exists,
- the game is still in the early phase,

research is restricted to:
- Construction
- Energy
- Weapons

Propulsion and Biotechnology are deliberately deferred until the early
Construction/Energy/Weapons base is stronger.

The decision report explicitly states when this doctrine is active.

## 3. Dynamic mineral cargo

The AI no longer strategically hard-codes `10/20/30`.

Two controlled Stars!-generated load samples established the exact small-load
quantity bytes:

- `10/20/30 -> 0A 14 1E`
- `20/20/20 -> 14 14 14`

v6.8 therefore calculates a desired Ironium/Boranium/Germanium shipment from:
- source surplus
- destination deficit
- current/near-term factory Germanium demand
- queued defenses
- queued strategic ships
- transport cargo capacity
- route distance

The selected exact quantities are written to the three mineral load bytes.

Current native safety boundary:
- 0..255 kT per mineral is enabled
- larger single-mineral loads remain blocked until the large-load encoding is
  separately validated

## 4. Germanium-aware logistics

Germanium receives first priority when transport capacity is constrained because
it directly gates factory construction.

A destination with useful factory headroom and insufficient Germanium will
therefore outrank a mineral-comfortable planet.

The source retains a working mineral reserve rather than being drained.

## 5. Cargo capacity

Stock base hull cargo capacities are reconstructed from the same hull data used
by StarsAPI.

Examples:
- Small Freighter: 70 kT
- Medium Freighter: 210 kT
- Large Freighter: 1200 kT
- Super Freighter: 3000 kT
- Privateer: 250 kT
- Rogue: 500 kT
- Galleon: 1000 kT

Current capacity confidence is conservative:
- base hull cargo capacity is counted
- additional cargo-pod component capacity is not yet credited

This may underfill a modified freighter but should not knowingly overload one.

## 6. Fuel calculations include the planned load

Transport fuel planning now evaluates the fleet after adding the planned
mineral cargo mass.

An empty freighter can no longer be classified as fuel-safe and then overloaded
into an unsafe route.

## 7. Destination Transport task

The v6.7 validated destination policy remains:

- Unload All Ironium
- Unload All Boranium
- Unload All Germanium
- Unload All Population
- Load Optimal Fuel

Stars! applies the policy automatically when the waypoint is reached.

## 8. Design-development planning

The AI now evaluates capability gaps and creates semantic design-development
proposals, including:
- Fuel Mizer long-range scout
- higher-capacity strategic transport
- Fuel Mizer colony ship
- real shipyard/refuel Space Dock when only Orbital Forts exist
- later Ultra Station support base
- newer combat hulls as Weapons/Energy improve

The decision report contains a new `DESIGN DEVELOPMENT` section explaining:
- requested role
- desired hull
- desired engine
- design objectives
- why the existing fleet architecture is inadequate

## Important native-design safety boundary

Brand-new native design creation is NOT yet enabled.

StarsAPI can decode DesignChangeBlock but its public implementation explicitly
does not implement `encode()` for design changes. Earlier uncontrolled
new-design attempts also corrupted native files.

Therefore v6.8:
- reasons about when a new design should exist
- specifies what it should do
- does NOT write a new design slot until a clean controlled Stars!-generated
  new-design sample is validated

This preserves the project rule: never guess native bytes where corruption is
possible.
