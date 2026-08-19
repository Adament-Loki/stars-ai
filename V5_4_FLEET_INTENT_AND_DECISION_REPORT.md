
# v5.4 Fleet Intent + Decision Report

## Fleet activity invariant

Every owned fleet must be accounted for every turn.

Possible states:

```
MOVE
CONTINUE WAYPOINT
REPOSITION
REPOSITION FOR COLONIZATION
HOLD / DEFEND
BLOCKED
```

There is no silent idle state.

Armed/combat fleets may intentionally hold at a planet when no higher-priority
mission exists. Unarmed fleets are expected to move, continue a waypoint, or
have an explicit role-specific limitation surfaced as BLOCKED.

Because native task coverage is still incomplete, the AI does not fabricate
mine-laying, remote-mining, cargo-transfer or Colonize task bytes. Where safe,
it uses validated simple movement to reposition those fleets toward useful
objectives.

## Decision report

Every player / every turn writes:

```
logs\turn-001-player-01-DECISION_REPORT.txt
```

The report uses:

```
Object Name - Action - Reason/Justification
```

Sections:

```
FLEETS
PLANET PRODUCTION
RESEARCH
NATIVE WRITER LIMITATIONS / SKIPPED ACTIONS
ACTIVITY CHECK
```

Example:

```
Scout 1 - MOVE -> Vega - Local-first reconnaissance of nearest unknown planet.
Colony 1 - REPOSITION FOR COLONIZATION -> Rigel - Move toward viable 82% world.
Escort 1 - HOLD / DEFEND - No higher-priority military mission; protect local space.

Homeworld - BUILD 3x factory, 2x mine - Improve early industrial throughput.
Empire Research - PROPULSION 100% - Expansion plan values mobility and engine tech.
```

The same report is printed to PowerShell during autoplay.

This is a decision-rationale/debug report. It intentionally does not expose
private hidden chain-of-thought.
