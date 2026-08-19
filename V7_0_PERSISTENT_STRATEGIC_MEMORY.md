
# Stars! AI v7.0 — Persistent Strategic Memory

v7.0 addresses the primary strategic failure found in the 75-turn benchmark:
the AI repeatedly forgot previously explored planets because each turn was
planned from only the current M-file snapshot.

This release implements enhancement items 1–4 from the Turn-75 postmortem.

## 1. Persistent native-player memory

Each managed player now has a persistent strategic memory file.

Default location:

```text
<seed-dir sibling>\<seed-dir-name>-stars-ai-state\
    player-01-memory.json
    player-02-memory.json
```

The directory is outside `output_dir`, so diagnostic cleanup does not erase
learned strategic state.

`WindowsAutoHostConfig` also accepts:

```json
"ai_state_dir": null
```

Set it to an explicit path if desired.

Memory automatically resets when:
- game ID changes;
- player seat changes;
- the game is rewound to an earlier year for a new playtest.

## 2. Ever-observed planet catalog

Every genuine current-M observation is merged into persistent planet intel.

For every known planet the AI retains:

- first seen year;
- last seen year;
- last known owner;
- race-relative habitability;
- environment;
- mineral concentrations;
- population/installations when known;
- surface minerals when known;
- starbase presence;
- coordinates/name.

If a planet is absent from a later sparse M-file, v7.0 restores that strategic
knowledge and marks it as stale memory rather than converting it back to an
unexplored world.

Important metadata:

```text
intel_source = current_m | persistent_memory
intel_age_years = N
```

Therefore `ever_observed` is monotonic during a normal forward-running game.

## 3. Cross-turn scout revisit suppression

The existing same-turn scout deconfliction remains.

v7.0 adds persistent target history:

```text
planet id
last assigned year
assignment count
last scout fleet id
```

A recently assigned unresolved target receives a three-turn cooldown.

More importantly, once a planet is actually observed it remains `observed=True`
for strategic purposes even when later M files are sparse. That removes the
75-turn ping-pong failure where hundreds of scan orders covered only a handful
of unique worlds.

A synthetic ten-turn sparse-M regression now requires ten consecutive scout
assignments to ten different planets.

## 4. Strategic exploration/colonization watchdog

The opening game deliberately uses HARD NUMBERS rather than galaxy percentages.

### Opening milestones

| Deadline | Minimum explored | Optimal explored | Minimum new colonies | Optimal new colonies |
|---|---:|---:|---:|---:|
| Turn 5 | 5 | 10 | 1 | 2 |
| Turn 10 | **10** | **25** | **3** | 5 |
| Turn 15 | 20 | 35 | 5 | 8 |
| Turn 25 | 35 | 50 | 8 | 12 |

The Turn-10 floor and optimal exploration target, and the three-new-colony
minimum, are explicit requirements from the playtest review.

After Turn 25, exploration goals switch to galaxy-size percentages:

| Deadline | Minimum explored | Optimal explored | Minimum new colonies |
|---|---:|---:|---:|
| Turn 40 | 45% | 55% | 12 |
| Turn 55 | 60% | 67% | 16 |
| Turn 75 | 70% | 80% | 20 |

Colonization remains a hard count because viable-world density is strongly
race- and map-dependent.

## Watchdog feedback

Every turn the decision trace now records:

```text
explored_count
explored_percent
new_colonies
owned_planets
discoveries_last_5_turns
scan_orders_total
unique_scan_targets_total
scan_target_reuse_ratio
exploration_pressure
colonization_pressure
next milestone
```

If the empire is approaching a milestone while behind:
- scout mission priority increases;
- auxiliary reconnaissance range expands;
- executable colony operations receive greater priority.

This release does NOT yet implement the next-sprint features such as
coverage-based scout fleet sizing or colony-ship production targets. v7.0 first
validates that the AI can remember and use what it learns.

## Native X lifecycle

v7.0 retains the current native writer behavior from v6.8.2:
- fresh per-turn X salt;
- validated template-derived SaveAndSubmit payload;
- current three-block controlled Save+Submit transaction;
- full FileHash rebuild;
- scout same-turn deconfliction;
- dynamic cargo;
- race-aware strategy.

## Validation

Regression suite includes:
- sparse-M planet memory restoration;
- monotonic known-world count;
- memory reset on game rewind;
- newly colonized world accounting;
- recent scan-target cooldown;
- Turn-10 hard-number goal validation;
- post-Turn-25 percentage-goal validation;
- ten-turn persistent exploration campaign with no repeated target.

The release must still be validated against a real 75-turn Stars! host run.
Python regression testing cannot substitute for the Windows Stars! executable
and native host lifecycle.
