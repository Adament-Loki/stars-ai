# Phase 9 resource inventory and migration

This is the pre-move classification record for every tracked Phase 9 resource
pattern. Scenario resources are immutable test/playtest inputs; operational
seeds and archival evidence are deliberately retained outside `TestResource`.

## Sandboxes

| Old location | Classification | New location | Why |
| --- | --- | --- | --- |
| `sandbox/` | Native client-capture fixture | `tests/TestResource/sandboxes/sandbox-native-reference/` | Codec/capability evidence and examples. |
| `sandbox-ai-decision-review-v119/` | Integration sandbox | `tests/TestResource/sandboxes/sandbox-ai-decision-review-v119/` | Scenario input. |
| `sandbox-combat-cap-v117/` | Integration sandbox | `tests/TestResource/sandboxes/sandbox-combat-cap-v117/` | Scenario input. |
| `sandbox-defense-counter-v123/` | Integration sandbox | `tests/TestResource/sandboxes/sandbox-defense-counter-v123/` | Scenario input. |
| `sandbox-design-policy-v116/` | Integration sandbox | `tests/TestResource/sandboxes/sandbox-design-policy-v116/` | Scenario input. |
| `sandbox-idle-freighter-v122/` | Integration sandbox | `tests/TestResource/sandboxes/sandbox-idle-freighter-v122/` | Scenario input. |
| `sandbox-ownership-purge-v121/` | Integration sandbox | `tests/TestResource/sandboxes/sandbox-ownership-purge-v121/` | Scenario input. |
| `sandbox-priority-fixes-v120/` | Integration sandbox | `tests/TestResource/sandboxes/sandbox-priority-fixes-v120/` | Scenario input. |
| `sandbox-scout-colony-v115/` | Integration sandbox | `tests/TestResource/sandboxes/sandbox-scout-colony-v115/` | Scenario input. |

## Autoplay JSON

| Old location | Classification | New location | Why |
| --- | --- | --- | --- |
| `autoplay-config.json` | Primary runtime configuration | unchanged | Canonical native-autoplay entry configuration. |
| `autoplay-config.example.json` | Official example | unchanged | Documented starting point for a host run. |
| `playtest-config.json` | Primary in-process playtest configuration | unchanged | Runtime harness configuration, not native autoplay. |
| `ai-decision-review-v119-resume.json` | Test scenario | `tests/TestResource/autoplay/ai-decision-review-v119-resume.json` | Historical controlled scenario. |
| `ai-decision-review-v119-s01.json` | Test scenario | `tests/TestResource/autoplay/ai-decision-review-v119-s01.json` | Historical controlled scenario. |
| `combat-cap-v117-autoplay.json` | Test scenario | `tests/TestResource/autoplay/combat-cap-v117-autoplay.json` | Historical controlled scenario. |
| `combat-cap-v117-resume-autoplay.json` | Test scenario | `tests/TestResource/autoplay/combat-cap-v117-resume-autoplay.json` | Historical controlled scenario. |
| `defense-counter-v123-autoplay.json` | Test scenario | `tests/TestResource/autoplay/defense-counter-v123-autoplay.json` | Historical controlled scenario. |
| `design-policy-v116-autoplay.json` | Test scenario | `tests/TestResource/autoplay/design-policy-v116-autoplay.json` | Historical controlled scenario. |
| `idle-freighter-v122-autoplay.json` | Test scenario | `tests/TestResource/autoplay/idle-freighter-v122-autoplay.json` | Historical controlled scenario. |
| `ownership-purge-v121-autoplay.json` | Test scenario | `tests/TestResource/autoplay/ownership-purge-v121-autoplay.json` | Historical controlled scenario. |
| `priority-fixes-v120-autoplay.json` | Test scenario | `tests/TestResource/autoplay/priority-fixes-v120-autoplay.json` | Historical controlled scenario. |
| `scout-colony-v115-autoplay.json` | Test scenario | `tests/TestResource/autoplay/scout-colony-v115-autoplay.json` | Historical controlled scenario. |

Scenario JSON paths are relative to their own file and are resolved by
`load_windows_autohost_config()`, not the shell current directory.

The unreferenced initial-baseline exports `out/native-bridge/AI-gamestate.json`
and `AI-orders.json` were removed; they are generated output recoverable from
Git history, not fixtures or runtime input. Future `out/` output is ignored.

## Persistent state

| Old location | Classification | New location | Why |
| --- | --- | --- | --- |
| `.ai-state-combat-cap-v117/` | Test memory fixture | `tests/TestResource/ai-state/combat-cap-v117/` | Controlled combat scenario. |
| `.ai-state-decision-review-v119/` | Test memory fixture | `tests/TestResource/ai-state/decision-review-v119/` | Controlled decision-review state. |
| `.ai-state-design-policy-v116/` | Test memory fixture | `tests/TestResource/ai-state/design-policy-v116/` | Controlled design state. |
| `.ai-state-idle-freighter-v122/` | Test memory fixture | `tests/TestResource/ai-state/idle-freighter-v122/` | Controlled logistics state. |
| `.ai-state-ownership-purge-v121/` | Test memory fixture | `tests/TestResource/ai-state/ownership-purge-v121/` | Controlled intelligence state. |
| `.ai-state-priority-fixes-v120/` | Test memory fixture | `tests/TestResource/ai-state/priority-fixes-v120/` | Controlled production state. |
| `.ai-state-scout-colony-v115/` | Test memory fixture | `tests/TestResource/ai-state/scout-colony-v115/` | Controlled scout/colony state. |

## X templates

| Old location | Classification | New location | Why |
| --- | --- | --- | --- |
| `.x-template-combat-cap-v117/` | Test-only native template | `tests/TestResource/native/x-templates/combat-cap-v117/` | Captured scenario input. |
| `.x-template-decision-review-v119/` | Test-only native template | `tests/TestResource/native/x-templates/decision-review-v119/` | Captured scenario input. |
| `.x-template-defense-counter-v123/` | Test-only native template | `tests/TestResource/native/x-templates/defense-counter-v123/` | Captured scenario input. |
| `.x-template-design-policy-v116/` | Test-only native template | `tests/TestResource/native/x-templates/design-policy-v116/` | Captured scenario input. |
| `.x-template-idle-freighter-v122/` | Test-only native template | `tests/TestResource/native/x-templates/idle-freighter-v122/` | Captured scenario input. |
| `.x-template-ownership-purge-v121/` | Test-only native template | `tests/TestResource/native/x-templates/ownership-purge-v121/` | Captured scenario input. |
| `.x-template-priority-fixes-v120/` | Test-only native template | `tests/TestResource/native/x-templates/priority-fixes-v120/` | Captured scenario input. |
| `.x-template-scout-colony-v115/` | Test-only native template | `tests/TestResource/native/x-templates/scout-colony-v115/` | Captured scenario input. |

There were no checked-in production X templates. Runtime templates remain
dynamic, game-specific native resources generated beside validated seed data.
The runtime MOD data moved from `src/stars_ai/data_*.mod` to
`src/stars_ai/native/resources/mod/` and is included as package data.

## Deliberately retained resources

- `playtests/seed/`: native functional seed games used by runtime/playtest workflows.
- `playtests/runs/latestdemo/`: checked-in forensic playtest evidence.
- Its archived logs retain historical absolute paths to the former root
  `sandbox/`; they are immutable evidence, not active configuration or lookup.
- `examples/`: public JSON examples, not test-only fixtures.
- `.research-starsapi/` and `stars-*-patch/`: reverse-engineering/history artifacts.
- `autoplay-config.json`, `autoplay-config.example.json`, and `playtest-config.json`:
  documented application configurations.
