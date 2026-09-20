# Baseline behavior contract

This document freezes observable behavior before the source-layout refactor.
It does not authorize strategy changes, native-order changes, test expectation
changes, or cleanup of existing failures.

## Baseline identity

- Branch: `refactor/00-baseline-contract`
- Starting commit: `194aaf536dade6cd9e914ab3d6001d4bd161ba23` (`playtest updates`)
- Test runner: `.venv\\Scripts\\python.exe -m pytest -q`
- Captured: 2026-09-20

## Full-suite result before contract tests were added

| Status | Count |
| --- | ---: |
| Passed | 649 |
| Failed | 6 |
| Skipped | 4 |
| Elapsed | 31.72 seconds |

The six failures are baseline failures, not part of this phase:

- `tests/test_expansion_hubs_v78.py::test_remote_orbital_fort_becomes_a_concrete_fuel_hub_build`
- `tests/test_objective_transport_v83.py::test_population_demand_builds_transport_that_can_use_current_surplus`
- `tests/test_objective_transport_v85.py::test_population_demand_builds_transport_that_can_use_current_surplus`
- `tests/test_objective_transport_v85.py::test_overfull_homeworld_population_demand_builds_high_capacity_transport`
- `tests/test_purposeful_fleet_invariants_v59.py::test_colony_stays_home_when_only_unknown_worlds_exist`
- `tests/test_safe_native_writer_v492.py::test_writer_does_not_invent_unknown_save_and_submit_payload`

The skipped tests are external-fixture tests. A behavior-preserving refactor
must retain these six failures (unless separately authorized to fix them) and
must not introduce a new failure in the existing suite.

## Post-contract verification

After adding the two behavior-contract tests, the complete suite reported
**651 passed, 6 failed, 4 skipped in 10.98 seconds**. The failed and skipped
sets were unchanged; the two additional passes are the contract tests
themselves.

## Fixed-turn capture contract

`stars_ai.regression_contract` provides two non-invasive capture paths:

- `capture_semantic_turn(state)` records a copy of the normalized input,
  complete semantic `OrderSet`, fleet intents, memory before/after/delta, and
  waypoint diagnostics. Native emitted/skipped lists are explicitly empty in
  normalized-only mode.
- `capture_native_turn(...)` delegates to the existing native writer and
  records the same data plus its emitted actions, skipped actions, waypoint
  diagnostics, and native transaction fingerprint.

The checked-in semantic fixture is:

```text
tests/regression/fixtures/expansion_turn/
  fixture.json                 fixed source and declared coverage
  semantic_contract.json       SHA-256 of the complete normalized capture
```

`tests/regression/test_refactor_behavior_contract.py` recreates the capture
from `examples/player2-turn2405.json` and compares its full-capture fingerprint
and cardinalities. To inspect an intentional future difference rather than
only its hash, use `write_contract_snapshot(capture, path)` to produce a JSON
review artifact; ordinary tests never rewrite the approved baseline.

## Native transaction fingerprint

`fingerprint_native_transaction(path)` reads the existing X file through the
current decoder, then hashes the ordered set of actual decrypted native order
records (type ID and body). It deliberately excludes:

- Type 8 header data, which carries the fresh encryption salt;
- Type 9 `FileHash`, which is derived from the order stream;
- Type 0 terminator.

It retains the full payload and sequence of every order block, so a changed
order type, payload, addition, removal, or reordering changes the fingerprint.
`tests/regression/fixtures/native_transaction/transaction.json` verifies both
the salt/FileHash invariance and order-change sensitivity without adding a
second encoder or changing Stars! wire behavior.

## Representative behavioral corpus

| Area | Fixed or representative fixture |
| --- | --- |
| Exploration | `expansion_turn`; `tests/test_persistent_exploration_campaign_v70.py` |
| Colonization | `expansion_turn`; `tests/test_colony_logistics_v56.py` |
| Economy/production | `expansion_turn`; `tests/test_resource_management_v90.py` |
| Research | `expansion_turn`; `tests/test_research_capabilities_v81.py` |
| Transport | `expansion_turn`; `tests/test_shared_transport_v103.py` |
| Military/defense | `expansion_turn`; `tests/test_territorial_minefield_doctrine_v109.py` |
| Ship design | `tests/test_ship_design_golden_v111.py` |
| Persistent memory | `expansion_turn`; `tests/test_persistent_intel_v70.py` |
| Waypoint continuation | `expansion_turn`; `tests/test_multi_waypoint_routes_v73.py` |
| Native writer safety | `tests/test_safe_native_writer_v491.py` |

## Verification commands

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q tests/regression/test_refactor_behavior_contract.py
```

No strategy module and no native order encoder was changed to establish this
baseline. Future refactor phases should run both commands before and after
each relocation, compare the existing-suite result to the recorded baseline,
and review any contract-fingerprint change explicitly.
