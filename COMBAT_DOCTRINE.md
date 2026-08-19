
# Combat Intelligence & Modernization Doctrine v2.9

The AI no longer treats military power as raw fleet mass or ship count alone.

## Ship-level understanding

Each known design is classified and scored for:

- beam weapon strength
- torpedo/missile strength
- shields
- armor
- accuracy
- initiative
- combat speed
- weapon-range profile
- tech generation
- resource cost
- effective combat value
- combat value per resource
- obsolescence relative to known enemy designs

Weapon doctrine is classified as:
- BEAM
- TORPEDO
- MIXED
- NONE

## Fleet comparison

Fleet value aggregates actual design counts. This means:

```
60 obsolete destroyers
```

can correctly evaluate below:

```
20 modern battleships
```

even though raw ship count is much larger.

The military comparison calculates:
- total combat value
- modern combat value
- obsolete combat value
- modern fleet fraction
- average relative technology
- expected trade ratio

## Modernization choices

The doctrine can choose:

- BUILD_CURRENT
- FIGHT_NOW
- TECH_THEN_REBUILD
- HOLD_AND_TECH
- RETREAT_AND_PRESERVE

`TECH_THEN_REBUILD` is selected when:
- current ships trade poorly
- a materially better design is reachable soon
- the expected temporary territorial loss is within the persona's sacrifice budget
- no critical core world is at immediate risk

This implements deliberate strategic sacrifice:

```
Do not replace obsolete destroyer losses.
Hold the core.
Allow low-value fringe territory to fall if required.
Research Weapons/Construction/Electronics.
Introduce a superior hull.
Rebuild and re-enter combat.
```

Core/high-sunk-cost territory overrides this logic and can force emergency defense.

## Research catch-up

`infer_research_gaps()` ranks military research needs based on:
- enemy-vs-own tech gap
- observed beam/torpedo doctrine
- observed shields/armor
- weapon/construction/electronics importance

## Accuracy caveat

The current evaluator is doctrine-aware but is not yet an exact Stars! battle simulator.
Where the MOD/StarsAPI database exposes concrete damage, shield, armor, initiative,
accuracy, range and cost data, those values can plug directly into the evaluator.

The next refinement should port exact component statistics and battle-board mechanics.
