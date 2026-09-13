# 100-turn AI decision review

Fresh native game from `playtests/seed/5small`, played as ten independently
audited ten-turn segments. P1 and P2 were `Balanced`; the other seed players
remained external controllers. All 100 host generations completed successfully.

## Checkpoints

| Game turn | P1 worlds | P2 worlds | P1 population | P2 population | P1 factories | P2 factories |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 6 | 5 | 1,881 | 3,930 | 34 | 78 |
| 20 | 11 | 10 | 3,861 | 7,644 | 159 | 328 |
| 30 | 15 | 11 | 6,285 | 11,049 | 474 | 612 |
| 40 | 15 | 11 | 10,711 | 12,684 | 858 | 1,124 |
| 50 | 16 | 11 | 13,588 | 14,387 | 1,312 | 1,389 |
| 60 | 18 | 9 | 17,023 | 16,142 | 1,577 | 1,432 |
| 70 | 16 | 8 | 19,981 | 17,744 | 1,897 | 1,548 |
| 80 | 12 | 5 | 23,067 | 18,941 | 2,222 | 1,763 |
| 90 | 13 | 5 | 25,720 | 20,069 | 2,538 | 1,948 |
| 100 | 6 | 3 | 15,083 | 7,375 | 1,458 | 705 |

P1 peaked at 18 worlds on turn 60 and P2 at 11 worlds on turns 20–50. The
leaders at turn 100 were P5 with 57 worlds / 20,438 factories and P6 with 34
worlds / 10,250 factories.

## Prioritized implementation backlog

1. **Make colony activation mandatory when a viable target exists.**
   Numerous fleets reported `HOLD / COLONY READY` with a named viable target
   but no load-and-colonize operation: P2 had four such fleets for planet 409
   at turn 40, P1 had four for Bloop/Mirror at turn 60, and P2 had three for
   Abel at turn 90. A colony ship must reposition to an eligible export world,
   load, and depart; it must not remain indefinitely in this state.

2. **Replace generic military holds with threatened-world response and
   counter-expansion.** P5 captured four P1 worlds and one P2 world during
   turns 61–70, then four more P1 worlds and three P2 worlds during turns
   71–80. P1 nevertheless issued generic `HOLD / DEFEND` orders to most of
   its 30–38 available/queued escorts. Add concentration, interception,
   evacuation, defensive-base support, and legal population-invasion target
   selection (excluding Alternate Reality) once neutral expansion is exhausted.

3. **Reconcile ownership loss immediately in planning memory.** The writer
   correctly rejected production on worlds no longer owned, but the planner
   continued to propose it for many turns. At turn 100 P1 still proposed
   queues at Himshaw, Mocha, Luscious, Fubar, and Bloop; P2 still proposed
   queues at Mozart, Maple Syrup, Hammer, and Abel. Prune lost worlds, their
   queues, export commitments, and pending command expectations before every
   strategic pass.

4. **Close native-writer execution gaps.** Repeated refuel/return retargets
   were skipped because `WaypointChangeTask` replacement was disabled.
   Remote-mining tasks were also repeatedly skipped (three examples per
   player late in the run). Support the observed transitions or prevent the
   planner from counting an unsupported action as available. Also serialize
   Type-27 design creation deliberately: only one mutation can be emitted per
   turn, so extra design proposals must remain queued rather than being treated
   as immediately obtainable.

5. **Return idle freight to useful export hubs.** P1 repeatedly issued
   `HOLD FOR LOGISTICS` to empty freighters (for example fleets 8 and 18 at
   turns 80–100), while P2 had six such holds at turn 90. This contradicts the
   intended exporter-return policy and leaves population/mineral capacity out
   of position. When no delivery is ready, select the highest-surplus,
   demand-connected export world and return there.

## Positive controls to preserve

- Both players expanded strongly through turn 30 and built factories quickly.
- Population shipments above the former small-load cap executed, including
  P1's 75,000-colonist delivery at turn 30.
- The research policy correctly favored better weapons over same-gun hull
  variants: P1 progressed from Weapons 10 at turn 40 to Weapons 23 at turn
  100; P2 likewise pursued Weapons 13–20.
- The archive and host audits completed successfully for every generation.

## Source checkpoints

Each segment retains its observer report, per-player decision report, native
orders, and archived game state under `segment-01` through `segment-10`.
