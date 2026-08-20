# v8.1 capability-driven research

Research is now planned as a named strategic capability before planetary
production is generated. It no longer balances the six fields toward similar
levels.

## Decision model

Each candidate records:

- a stable capability id and name;
- its complete six-field requirements;
- remaining requirements from current tech;
- strategic need, urgency, value, and expected utilization;
- an estimated completion horizon and confidence;
- the concrete action intended after unlock.

The initial authoritative catalog is derived from bundled data rather than
duplicated tech constants. It includes Medium/Large/Super Freighters, Space
Dock and Ultra Station hulls, the IFE Fuel Mizer threshold, and every standard
or Total Terraforming breakpoint already modeled by the engine. Strategy
modules may also provide structured demands through
`state.native["research_demands"]`.

The selected posture is one of `EXPANSION_FIRST`, `TARGETED`, `SPRINT`,
`MILITARY_EMERGENCY`, `MATURE_SURGE`, or `RECOVERY`. Expansion debt discounts
nonessential research while retaining expansion-enabling hull, hub, and
terraforming goals. A nearby military emergency may override the expansion
goal. Persistent memory applies a 25% challenger threshold to avoid field/goal
oscillation and ends a sprint that has exceeded its estimated horizon without
measurable progress.

## Research sprints and production safety

Normal autonomous research uses the empirically observed 15% setting. A
valuable capability approximately one to five tech-level turns away may use a
25% sprint. The planner identifies mature contributor planets before economy
planning. Economy then clears only noncritical queues on those contributors.

The following remain protected:

- current custom ship or starbase builds;
- shipyards needed while expansion is behind;
- fragile new colonies;
- production needed by a military emergency.

Protected shipyards use the validated PlanetChange leftover-only ON command,
so their critical queue remains intact while surplus resources can contribute.
The native OFF form remains unsupported because it has not been validated.

## Correct native encoding

ResearchChange Type 34 is now encoded and parsed as:

- byte 0: actual global percentage (`0F` = 15, `19` = 25);
- byte 1 high nibble: next field;
- byte 1 low nibble: current field;
- field enum: Energy 0, Weapons 1, Propulsion 2, Construction 3,
  Electronics 4, Biotechnology 5.

Validated examples covered by regression tests include:

```text
15% Energy -> Construction       0F 30
15% Energy -> Weapons            0F 10
25% Construction -> Electronics 19 43
15% Electronics -> Weapons       0F 14
```

PlanetChange Type 35 leftover-only ON is emitted as planet id followed by
`01 00 00 00`; planet 40 therefore produces `28 00 01 00 00 00`.

## Turn-by-turn observability

Decision reports identify the named goal, posture, score, remaining tech,
horizon, current and next fields, percentage, contributors, protected worlds,
and post-unlock action. Persistent command expectations now verify current
field, next field, percentage, and leftover-only planet mode, producing the
same `COMPLETED`, `PENDING`, `WARNING`, or `UNVERIFIED` lifecycle used by fleet
and production commands.

## Verification

The v8.1 regression set covers named-capability selection, Large Freighter
sprints, critical colony/shipyard protection, IFE behavior, expansion debt,
military override, hysteresis, material challengers, stalled recovery, blocked
utilization, unlock tracking, exact Type 34/35 bytes, and status-aware command
verification. Full suite at implementation: **311 passed, 4 skipped**.
