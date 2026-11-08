# Refactor certification

## Scope and source identities

- Frozen Phase 0 behavioral-contract commit: `953eaf87b2d799f9e14abfd3be465baa8a338e3d`.
- Phase 0's original starting commit: `194aaf536dade6cd9e914ab3d6001d4bd161ba23`.
- Refactored code certified here: `c2d242d` (`fix: restore refactor behavior contracts`), based on Phase 9 commit `1c1a6a50642f19543c340c4fd1a87ec126176f7b`.
- Certification branch: `refactor/10-certification`.
- Date: 2026-09-22.

This is evidence for the behavior-preserving refactor only. It is not a strategy-tuning, native-format, or gameplay-balancing change.

## Corrections found during certification

Certification found two real regressions rather than treating a changed fixture as the new baseline.

1. Phase 3 changed the `PHASE:` separator from one em dash to the three mojibake code points `â€”`. That altered semantic `OrderSet.notes` and persisted strategic notes. The original separator and Phase 0 fixture fingerprint were restored.
2. Phase 7 removed the writer's old path parameter but left a reference to `m_path`. Native serialization therefore raised `NameError` before it could write a transaction. The writer now obtains the current M-file path from the native adapter's observed provenance, reads only its header, and fails clearly if that provenance is unavailable. It still neither plans nor decodes strategic state.

`tests/test_native_writer_boundary.py` now covers that adapter-provenance path. No native block layout, capability gate, FileHash treatment, or encryption algorithm was changed.

## Level A — fixed Phase 0 behavior

The Phase 0 expansion fixture was run directly from both the frozen worktree and the corrected refactor branch. All compared sections matched.

| Contract section | Result |
| --- | --- |
| Normalized input state | Match |
| Strategic phase and rationale | Match |
| Semantic orders and priorities | Match |
| Fleet intents | Match |
| Research decision | Match |
| Colonization decisions | Match |
| Persistent state after turn | Match |
| Scout routes, movement outcomes, and waypoint diagnostics | Match |
| Native emitted/skipped actions | Match |
| Native transaction fingerprint | Match |

The restored semantic capture fingerprint is `84ab96a8657c155d8c99ecae03238542f6483759082701be3053613fef36dc61`.

For the native reference fixture, the compared semantic-order, intent, emitted-action, skipped-action, pending-expectation, scout-route, waypoint, and transaction sections all matched. Its normalized transaction fingerprint was `8e19b785d558509ab4205f5c3fab439f714c8d948b0092b4fc8b5eea3f17cf0d`. That fingerprint intentionally ignores the fresh X-file salt and derived FileHash while retaining ordered native command semantics.

The focused contract and writer-boundary tests passed: **5 passed**.

## Level B — hosted lifecycle smoke matrix

The actual application workflow was exercised from the immutable `playtests/seed/5Comps11Total` seed with all four managed AI players, the integrated writer, pre-host audit, Stars! host, state commit/rollback, reports, history merge, and turn archive enabled.

| Hosted turns | Successful cycles | Years | Player decision reports |
| ---: | ---: | --- | ---: |
| 1 | 1/1 | 2400 to 2401 | 4 |
| 3 | 3/3 | 2400 to 2403 | 12 |
| 5 | 5/5 | 2400 to 2405 | 20 |

Each successful cycle produced all four X files, passed the pre-host audit, had its X files consumed by the host, advanced the observed year, merged and validated history, committed staged memory only after host success, and wrote the required archive phases.

## Level C — extended hosted playthrough

One continuous hosted lineage was run to 50 turns: a fresh 5-turn run, 20-turn validated play-on continuation, then a 25-turn validated play-on continuation. The combined evidence is:

| Check | Result |
| --- | --- |
| Host cycles | 50/50 successful |
| Years | 2400 to 2450 |
| Required archive phases | 50/50 complete |
| Decision reports | 200 (four per cycle) |
| Committed memory files at completion | 4 |
| Pending memory files at completion | 0 |
| Host crash / native rejection / transaction corruption | None in the certified lineage |

The 25-turn checkpoint (the first 5 turns plus the 20-turn continuation) ended at year 2425. It and the complete 50-turn run exercised the normal plan → stage → serialize → audit → host → verify → commit → report → archive lifecycle, including play-on/history handling.

Two independent fresh temporary host launches timed out before changing any native game file; their semantic decisions matched the successful run, and the legacy executable printed only its version. They were retained as environmental host-launch diagnostics, not accepted as successful tests. The successful short-path fresh runs above and the 50-turn lineage provide the passing lifecycle evidence. This intermittent legacy-host launch behavior remains a test-environment risk worth investigating separately.

## Report review

Player 1 and Player 4 journals from the extended run were inspected. A tester can identify the current objective and rationale, research focus, metric change, planetary production decisions, fleet destination/intent/status, semantic orders, blocked native actions, warnings, and previous-turn outcomes. The reports preserve uncertainty such as `UNVERIFIED` research-mode outcomes instead of claiming confirmation from unavailable native data.

One presentation limitation remains: the flat journal gives the count of native-emitted actions and an explicit table of skipped actions, but does not yet give every emitted action its own human-readable row. The complete emitted records remain present in the decision/native audit artifact. This is a reporting clarity follow-up, not a semantic or native-transaction difference.

## Architecture boundary review

The intended ownership is present and exercised:

| Boundary | Finding |
| --- | --- |
| `StarsAI` / `StarsAgent` | `StarsAI.run_turn()` is authoritative; `StarsAgent` is a compatibility wrapper. |
| Planning | `ai/planners` and the ordered pipeline own planner/policy orchestration. |
| Persistence | `AIStateStore` stages and commits or rolls back persistent memory. |
| Native layer | `native/x_writer.py` consumes `GameState` plus semantic `OrderSet`; it imports neither `StarsAI`, `StarsAgent`, `AgentMemory`, nor strategic planners. |
| Application lifecycle | Autohost owns observation, plan/write coordination, host invocation, commit/rollback, reporting, and archive capture. |
| Reporting | `reporting.py` renders the decision/report model outside the writer. |

The strict Phase 2 state boundary is **not fully complete**: `engine.py` still writes legacy strategic scratch values such as research strategy, watchdog, command outcomes, movement diagnostics, and strategic phase into `GameState.native` before mirroring them into `TurnAIState`. The typed state is there and behavior is equivalent, but `GameState.native` is still the legacy source for those values. Raw decoder/native provenance legitimately remains there; strategic scratch data should not.

## Full-suite result

Post-correction full suite:

| Status | Count |
| --- | ---: |
| Passed | 688 |
| Failed | 6 |
| Skipped | 4 |
| Time | 19.53 s |

The failure set is unchanged from the frozen baseline:

- `tests/test_expansion_hubs_v78.py::test_remote_orbital_fort_becomes_a_concrete_fuel_hub_build`
- `tests/test_objective_transport_v83.py::test_population_demand_builds_transport_that_can_use_current_surplus`
- `tests/test_objective_transport_v85.py::test_population_demand_builds_transport_that_can_use_current_surplus`
- `tests/test_objective_transport_v85.py::test_overfull_homeworld_population_demand_builds_high_capacity_transport`
- `tests/test_purposeful_fleet_invariants_v59.py::test_colony_stays_home_when_only_unknown_worlds_exist`
- `tests/test_safe_native_writer_v492.py::test_writer_does_not_invent_unknown_save_and_submit_payload`

The increased pass count is from tests added during the refactor, including the new writer-boundary test; it is not a changed baseline failure.

## Certification conclusion

The corrected refactor is certified for fixed-turn semantic behavior, persistent-state behavior, native transaction semantics, and the successful end-to-end hosted lifecycle evidenced above. There are no unexplained semantic, persistence, emitted/skipped, or native-fingerprint differences from the Phase 0 contract.

Do **not** call the architecture portion of Phase 10 fully closed yet: complete the small typed-turn-state migration out of `GameState.native`, and add a flat report emitted-action table. After those non-strategy follow-ups, re-run this same certification matrix. The next phase after that should be actual gameplay/strategy improvement, using the behavioral contract as its guardrail.

## Known limitations and recommended follow-up

- Fix the two remaining architectural/reporting closure items above without changing planner behavior or native encoding.
- Investigate the intermittent legacy-host fresh-launch timeout separately from AI correctness; retain timeout archive diagnostics in such investigations.
- `capture_native_turn()` has a pre-existing convenience-path collision when its default `*.trace.json` name is used with report path derivation. The explicit `*-decision-native.json` path used for this certification is sound. Correct the helper in a dedicated observability change rather than folding it into the refactor certification.
