
# v6.6 Starbase Capability, Germanium Logistics, Transport Delivery, and Planet-State Accuracy

## 1. Starbase capability is no longer a boolean

`has_starbase` is not sufficient to decide whether a planet can build ships or refuel fleets.

Stock starbase hulls are decoded by hull ID:

- 32 Orbital Fort
  - `can_build_ships = false`
  - `can_refuel = false`
- 33 Space Dock
  - `can_build_ships = true`
  - `can_refuel = true`
- 34 Space Station
  - `can_build_ships = true`
  - `can_refuel = true`
- 35 Ultra Station
  - `can_build_ships = true`
  - `can_refuel = true`
- 36 Death Star
  - `can_build_ships = true`
  - `can_refuel = true`

Unknown/custom base hulls are treated conservatively as neither refuel facilities nor shipyards.

Consequences:
- fuel routing will never divert a stranded fleet to an Orbital Fort expecting free refuel;
- objective ship production will not assign ships to an Orbital Fort planet;
- production diagnostics show base type, shipyard capability, and refuel capability.

This release does not yet model per-starbase ship-mass production limits.

## 2. Germanium is a first-class economic constraint

StarsAPI's race model confirms that a factory consumes:
- 4 kT Germanium normally;
- 3 kT with the Cheap Factories race option.

Mines consume resources but no surface minerals.

The production planner now:
- monitors current surface Germanium every year;
- reserves a small Germanium buffer for strategic ship construction;
- clamps factory quantity to the number the planet can actually pay for;
- prioritizes useful mines when Germanium is constraining factory development;
- reports Germanium surface stock, concentration, and per-factory cost.

Example:

`Germanium 12kT, factory cost 4kT, reserve 8kT -> at most 1 factory`

## 3. Germanium-aware transport logistics

The validated transport encoding remains intentionally narrow:
- load 10 Ironium / 20 Boranium / 30 Germanium;
- Transport task unload: all Ironium / 10 Boranium / 15 Germanium.

Freighter destinations are now prioritized by Germanium pressure:
- population-supported factory headroom;
- current surface Germanium;
- minimum local Germanium reserve.

A donor must have enough stock to perform the validated 10/20/30 load while preserving a local Germanium reserve.

## 4. Transport delivery is now a lifecycle

The original validated transport order intentionally leaves 10 Boranium + 15 Germanium aboard after the first all/10/15 unload.

Previously, the AI could treat that freighter as available for a new mission.

v6.6 changes the lifecycle:

1. load and travel;
2. first validated Transport unload occurs;
3. on arrival, if the known residual cargo is present, reissue the same validated Transport unload task at the current planet;
4. only after cargo is empty may the freighter receive a new route.

If the residual cargo does not match a form we have experimentally validated, the freighter HOLDS instead of guessing an unload encoding.

General arbitrary unload serialization remains unvalidated.

## 5. Canonical Stars! planet names

The complete 999-entry canonical planet-name table is now bundled.

Examples from the playtest:
- planet name ID 188 -> Coolidge
- 209 -> Crow
- 339 -> Genesis
- 734 -> Red Dwarf
- 989 -> Zebra

Logs, fleet destinations, colonization rankings, production, and transport reasons now use these friendly game names rather than `PlanetName#209` once map metadata is available.

## 6. Planet population unit correction

This is a significant correctness fix.

StarsAPI's PlanetBlock exact surface population value is in HUNDREDS of colonists.

The previous adapter normalized it as:

`raw * 1000`

v6.6 corrects this to:

`raw * 100`

Fleet cargo population remains separately normalized according to the fleet cargo representation used by the validated colony workflow.

Production diagnostics explicitly show:

`current-year M-file population raw=N x100 => normalized population`

so the conversion is auditable in every yearly run.

## 7. Current-year planet data wins

The native `.m#` reader already selects the final turn section in a multi-turn file.

When multiple observations of one planet exist inside that current turn, v6.6 now prefers:
1. the newest `observed_turn`;
2. then the richest exact record.

This prevents an older detailed planet record from defeating a newer current-year state when calculating:
- population;
- mines/factories;
- surface minerals;
- production caps.

## 8. Population has a theoretical carrying capacity

Stars! population growth is not indefinite exponential growth.

The model now follows the StarsAPI/Technical FAQ crowding curve.

Nominal race maximum population is 1,000,000, modified by race:
- Hyper Expansion: x0.5
- Jack of All Trades: x1.2
- Only Basic Remote Mining: x1.1

Applicable modifiers stack.

For positive-habitability planets:
- <=25% capacity: normal race growth x habitability;
- 25%-100%: growth falls with `16/9 * (1-capacity)^2`;
- 100%: zero growth;
- above 100%: crowding deaths.

Red planets use the special 25,000 population crowding ceiling.

Growth/death is rounded to 100-colonist units, matching StarsAPI's vanilla-Stars model.

The production report now includes:
- current exact population from this year's `.m#`;
- theoretical planet population capacity;
- percent of capacity;
- projected one-year growth;
- projected next population;
- current factory/mine operating caps.

Important: factory/mine BUILD limits remain based on CURRENT population, not speculative next-year growth.

## 9. Safety boundary

This version still follows the project's native-write rule:

Do not generalize a command from guessed bytes.

Therefore:
- arbitrary mineral transfer amounts remain disabled;
- arbitrary manual unload remains disabled;
- known transport remainder completion reuses only the already-observed all/10/15 Transport task.
