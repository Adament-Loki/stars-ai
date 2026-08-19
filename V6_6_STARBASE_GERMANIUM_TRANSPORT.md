
# v6.6 Starbase Capability + Germanium + Transport Delivery + Reporting

## Orbital Fort handling

`has_starbase` is no longer treated as synonymous with "can refuel" or
"can build ships."

Stock starbase hull capability model:

- Orbital Fort
  - no ship production
  - no fleet refueling
- Space Dock
  - ship production
  - fleet refueling
- Space Station
  - ship production
  - fleet refueling
- Ultra Station
  - ship production
  - fleet refueling
- Death Star
  - ship production
  - fleet refueling

Unknown/custom starbase hulls default conservatively to no refuel/no shipyard
until decoded.

Fuel diversion and ship-production planning now use these capabilities instead
of the old `has_starbase` flag.

## Germanium-aware factory planning

Factories consume surface Germanium, so factory production is now limited by:

- current population-supported factory headroom
- current surface Germanium
- race factory Germanium cost
- a small Germanium reserve for strategic builds

When Germanium is the limiting factor:
- mines are prioritized before factories when mine headroom exists
- factory quantities are clamped to what the planet can actually pay for
- transports prioritize Germanium-starved productive colonies

The production report shows:
- surface Germanium
- Germanium concentration
- Germanium cost per factory

## Transport delivery lifecycle

The previously validated cargo route loads:

- 10 Ironium
- 20 Boranium
- 30 Germanium

and uses the validated unload task:

- Ironium: all
- Boranium: 10
- Germanium: 15

That naturally leaves 0 / 10 / 15 after the first delivery.

v6.6 does NOT assign a new freight route while known residual cargo remains.
Instead, on the following turn it reissues the same empirically validated
Transport task at the current destination to complete the known residual unload.

If cargo is present in an unvalidated pattern, the freighter holds rather than
inventing transfer bytes.

## Friendly planet names

The parser now includes the complete canonical 999-name Stars! planet table.

Reports and decisions use friendly names such as:
- Crow
- Knob
- Magellan
- Quiche
- Serapa

rather than `PlanetName#<id>` whenever the name ID is valid.

## Population correction

The exact Stars! planet population field was previously normalized incorrectly.

StarsAPI's PlanetBlock representation stores exact population in units of
100 colonists.

v6.6 changes:

`planet.population = native_population * 100`

instead of:

`native_population * 1000`

Fleet population cargo remains in the separately validated thousand-colonist
representation used by the 25k colony-load command.

## Current-year production state

Annual production calculations now prefer the freshest current-M-file planet
record before older richer observations.

The production report explicitly logs:

- M-file source year
- raw population value
- normalization (`raw x100`)
- normalized population
- factories current / population-supported cap
- mines current / population-supported cap
- Germanium
- starbase hull
- shipyard Y/N
- refuel Y/N

This makes yearly economic recalculation auditable directly from the decision log.

## Validation

Full regression suite:
- 190 passed
