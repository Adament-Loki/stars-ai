
# Stars! AI v4.2 — First Controlled Playtest

## Objective

Validate that AI intent, native order execution, and resulting Stars! state stay aligned
for the first 50 turns. Winning is secondary.

## Players

1. Balanced JOAT
2. Expansionist JOAT
3. Super Stealth
4. Space Demolition

Use comparable economic race quality so PRT/persona behavior is the main variable.

## Checkpoints

- Turn 10
- Turn 25
- Turn 50

At each checkpoint capture:

- owned planets
- population
- factories
- mines
- fleets
- ships
- tech sum
- starbases
- gates
- design-slot use
- major battles
- battle-prediction vs actual outcome
- SS spy/raid/interception activity
- SD minefield deployment/detonation activity
- blocked/partial native actions
- intent-vs-actual mismatches

## First-run pass/fail criteria

### PASS
- Native files load without corruption.
- Validated orders execute as intended.
- AI players continue making turns.
- No player repeatedly oscillates between contradictory plans.
- SS performs at least one distinctive stealth/intelligence/economic-warfare behavior when opportunities exist.
- SD performs at least one distinctive mine-network behavior when opportunities exist.
- Economy expands without obvious population-transfer churn.
- No catastrophic design-slot deadlock.

### INVESTIGATE
- A validated order does not appear in the next game state.
- AI continuously replans the same fleet without progress.
- Freighters spend several turns in low-value repositioning loops.
- Combat prediction and actual Stars! result diverge materially.
- SS ignores obvious packet/freighter/mineral targets.
- SS raids so much that normal expansion collapses.
- SD lays huge static fields despite low threat.
- SD fails to detonate when a high-value enemy fleet enters a favorable field.
- Generic strategy overrides an obviously superior PRT-specific action.
- A native PARTIAL/BLOCKED action becomes strategically critical.

## Recommended workflow

For each turn:

1. Give each player only its own `.m#`, shared `.xy`, and its own prior memory/trace.
2. Run the AI.
3. Save semantic intent before native serialization.
4. Serialize only VALIDATED native actions automatically.
5. Copy `.x#` files to the host sandbox.
6. Host-generate the next turn.
7. Parse new player states.
8. Compare intended actions against observable results.
9. Log mismatches.
10. At checkpoint turns, read `.hst` only from the omniscient observer and produce the checkpoint report.

## Priority debug order

1. File corruption / parser failure
2. Validated native order mismatch
3. State reconstruction error
4. Strategy/native capability mismatch
5. Logistics/population oscillation
6. Combat prediction error
7. PRT-specific decision quality
8. Long-term competitive performance
