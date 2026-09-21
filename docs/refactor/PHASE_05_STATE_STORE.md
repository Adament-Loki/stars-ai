# Phase 5 persistent state boundary

`AIStateStore` owns the persistence transaction for one player's AI memory:

```text
load committed state -> stage pending state -> host succeeds: commit
                                      \-> host fails: rollback
```

The native writer continues to generate the pending JSON snapshot because that
is the existing point where semantic decisions, writer validation, and emitted
actions meet. The integrated autohost bridge adopts that exact pending file
into `AIStateStore`; it no longer performs file promotion or deletion itself.
This leaves native X encoding and writer safety checks unchanged.

The on-disk representation remains `AgentMemory` JSON. `PersistentAIState`
provides the typed conversion used by the store, so old saved files load under
the established schema migration and all current fields round-trip unchanged.
Unknown future fields are retained as opaque legacy metadata and written back
unchanged; they do not become AI strategy inputs merely by being preserved.

Memory behaviors have explicit service owners in `stars_ai.memory_services`:

- `IntelligenceMemoryService`
- `ActionOutcomeService`
- `ExplorationMemoryService`
- `KPIHistoryService`
- `MovementAuditService`

`AgentMemory` remains the compatibility facade and persisted data shape. Its
public behavior methods delegate to those focused services, while the
well-tested intelligence, action-outcome, and exploration helpers remain in
place as incremental-migration seams. KPI history and movement auditing have
moved fully to their focused services. This phase intentionally does not alter
strategy, scout route semantics, action outcome rules, or Stars! native
serialization.
