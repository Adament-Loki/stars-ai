
# Strategic Phase System v2.4

All AI personas now share a common opening doctrine:

1. Explore aggressively.
2. Expand aggressively.
3. Establish a viable territorial/economic base.
4. Use persona-specific planet selectivity to decide which worlds are worth taking.

The AI transitions away from pure expansion when open frontier shrinks, neighboring empires are encountered, or territorial saturation rises.

## Planet selectivity

`PlanetPreference` supports:
- minimum habitability
- minimum normalized resource richness
- selectivity threshold
- strategic/frontier exceptions

This allows one AI to claim almost any viable world while another waits for high-habitability or mineral-rich worlds.

## Post-frontier personas

- Militarist: `OPPORTUNISTIC_WAR` — find favorable conflicts and easiest valuable targets.
- Balanced/Fortifier: `FORTIFICATION` — develop border worlds, defenses, scanners, and defensive fleets.
- Industrialist: `INDUSTRIAL_BUILDOUT` — maximize factories/mines and economic throughput.
- Technologist: `TECH_ACCELERATION` — convert the secured empire into a research lead.
- Expansionist: default `CONSOLIDATION`, while still exploiting remaining open pockets.

The phase manager emits multipliers consumed by downstream research, economy, exploration, colonization, and military planners.
