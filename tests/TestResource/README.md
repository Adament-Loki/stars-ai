# TestResource

`TestResource` is the canonical home for durable, checked-in test inputs and
golden references. Resolve a fixture through
`tests.helpers.resources.test_resource(...)`; do not calculate a path from the
current working directory and do not add a new fixture at repository root.

| Directory | Contents | Test rule |
| --- | --- | --- |
| `sandboxes/` | Native client captures and historical integration sandboxes | Copy before a host or client can modify files. |
| `autoplay/` | Scenario-only autoplay configurations | Paths are relative to the configuration file; copy/configure output before running. |
| `ai-state/` | Persistent-memory fixture snapshots | Immutable; copy to `tmp_path` before staging, committing, or rolling back. |
| `native/x-templates/` | Captured test-only `.x#` template inputs | Immutable; never use as a live template directory. |
| `regression/` | Approved semantic and native-transaction contract fixtures | Never rewrite during ordinary test execution. |

Generated orders, hosted game files, reports, logs, copied templates, and
staged memory belong in a test framework temporary directory. Historical
playtest seeds and archived runs remain under `playtests/` because they are
runtime/forensic evidence, not automated-test fixtures.

Runtime X templates are not bundled as test data. They are derived from a
validated native seed and kept beside that seed by the autohost subsystem; see
`src/stars_ai/native/resources/x-templates/README.md`.
