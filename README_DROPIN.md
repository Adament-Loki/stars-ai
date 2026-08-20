# v8.7 STARSAPI DESIGNBLOCK DIAGNOSTIC BUILD

This cumulative package supersedes v8.6.1 for the next ship-creation playtest.  The embedded ship body is now encoded/decoded by a direct Python port of StarsAPI `DesignBlock`, while the still-empirical Type27 wrapper is isolated for a clean host experiment.

**If v8.6/v8.6.1 is already installed:** extract this ZIP over the repo and run:

```powershell
python .\APPLY_V87_STARSAPI_NATIVE_PATCH.py
pytest -q tests\test_starsapi_design_codec_v87.py tests\test_type27_client_turn3_v861.py tests\test_ship_design_v83.py
```

**From clean public main:** run:

```powershell
python .\APPLY_NATIVE_WRITER_PATCH.py
python .\APPLY_V87_STARSAPI_NATIVE_PATCH.py
python .\APPLY_COLONY_LAYER1_PATCH.py
```

Before hosting any AI X containing a design creation:

```powershell
python .\VERIFY_TYPE27_STARSAPI_V87.py .\path\to\GAME.x1
```

See `README_STARSAPI_TYPE27_V87.md` for the protocol evidence, controlled-sample results, and the temporary one-turn Type27 isolation behavior.

Build validation: `13/13` focused StarsAPI/Type27/design tests passed; `4/4` controlled client ship-design X files passed StarsAPI body inspection; all package Python files AST-parse; isolated full-X structural simulation passed with exact FileHash length.

---

# v8.6.1 IMPORTANT TYPE27 HOTFIX

This cumulative package includes the v8.6.1 fresh-create correction. For a new ship design in a free slot, native Type27 now uses `11 A0|slot` empty staging followed by `11 60|slot` populated final. See `README_TYPE27_V861.md` for the Turn-3 evidence and verifier procedure.

# Stars! AI v8.6 — Explicit Ship Design Lifecycle + Round-Trip Onion Logistics

**Base repository:** `Adament-Loki/stars-ai`  
**Base branch:** `main`  
**Base commit:** `1c45444abef9982ab6af6bc94cb48c96783bcbaf`  
**Package intent:** cumulative replacement for v8.5/HOTFIX1 and all earlier onion-research/population/starbase/ship-design drop-ins. v8.6 keeps native ship creation enabled but splits create/delete/recycle into independently guarded operations.

This release is aimed directly at the opening weakness seen against the built-in Stars! AI: convert population growth into territory and distributed industry faster, while keeping research focused on capabilities that actually support expansion.

---

## Install

Start from the public `main` commit above (or a checkout whose guarded files still have the expected blobs):

```powershell
cd C:\Dev\repos\stars-ai
git status
```

Extract/copy this ZIP over the repository root, preserving paths.

Then run **both guarded patchers**. The native patcher can rebase from the exact `x_writer.py.pre-v85.bak` created by v8.5 if the experimental v8.5 writer is currently active:

```powershell
python .\APPLY_NATIVE_WRITER_PATCH.py
python .\APPLY_COLONY_LAYER1_PATCH.py
```

They deliberately refuse to patch if the corresponding current-main file has drifted.

Expected current-main Git blobs:

```text
src/stars_ai/native/x_writer.py    8d2c8d3216b4f8868a8b1952672116951d1dd763
src/stars_ai/colony_planner.py     ee212ee883f93ebc8004f19fef58635558baf0aa
```

Backups are preserved/created before modification:

```text
src/stars_ai/native/x_writer.py.pre-v85.bak          # original v8.5 baseline if present
src/stars_ai/native/x_writer.py.pre-v86-current.bak  # quarantines current v8.5 writer when rebasing
src/stars_ai/native/x_writer.py.pre-v86-baseline.bak # exact public-main serializer baseline
src/stars_ai/colony_planner.py.pre-v85.bak
```

Then run:

```powershell
pytest -q
```

### Validation performed while building this package

Focused cumulative strategy/design suite:

```text
89 passed
0 failed
```

Full regression checkout:

```text
302 passed
4 fixture-only tests unavailable
0 code failures
```

The four unavailable tests require external `/mnt/data/AI.xy` / `AI.m1/x1` and `AI(2).*` fixtures that are not present in this runtime. The exact user-provided `GAME.x2` free-slot Privateer Type27 transaction is embedded as a golden byte fixture in `tests/test_ship_design_lifecycle_v86.py`.

---

# 1. Opening population doctrine — 20,000-colonist pulses

Economic population freight is separate from the small colonization packet.

For normal early economic redistribution:

```text
20,000 colonists = 200 kT population cargo
```

A source becomes eligible for a pulse when it can send the full 20,000 colonists while preserving its protected population floor.

### Homeworld opening rule

```text
HW population < 100,000
    -> do not export economic population

HW population >= 100,000
    -> one 20,000-colonist / 200-kT shipment may depart
    -> target post-load population ~80,000 or better
    -> let the HW replenish before another pulse
```

This is intentionally aggressive micro rather than continuous draining.

### One population departure per source per turn

This is a hard phasing invariant.

If several empty freighters are waiting at one exporter, they do **not** independently load population.

Example:

```text
HW = 105k
Transport A -> load 20k -> projected HW 85k
Transport B -> wait
Transport C -> wait or reposition to another active exporter
```

Even when a world has a larger stored surplus, the default opening policy remains one population dispatch per source per turn. This protects breeder growth and naturally spaces the transport fleet.

---

# 2. Formal Layer-1 program — seek 4–5 good hubs

The opening onion program explicitly tries to establish:

```text
4 minimum / 5 target Layer-1 hubs
```

roughly in the first useful ~65–190 ly band around the homeworld.

This is **not** a colonization quota that overrides planet quality.

The guarded `colony_planner.py` patch adds a Layer-1 strategic bonus only **after** the existing race-adjusted habitability, terraforming, mineral-resource and support-distance eligibility rules have accepted a planet.

So the AI should prefer good first-ring hub worlds, not colonize bad worlds just to reach five.

---

# 3. Layer-1 graduation

A designated Layer-1 world graduates when both are true:

```text
population >= ~25% of race-adjusted planet capacity
AND
operational support starbase exists that can:
    build ships
    refuel fleets
```

After graduation:

```text
HOMEWORLD STOPS FEEDING THAT HUB
```

The graduated Layer-1 hub becomes a valid exporter/parent for Layer-2 worlds.

A graduated world is **not** allowed to export a 20k pulse if doing so would push it back below its protected floor.

Example for a 1,000,000-capacity world:

```text
25% floor = 250k
250k -> graduated, but no 20k export yet
270k -> may send 20k -> remains 250k
```

This is the intended cascade:

```text
HW
 |-20k-> L1-A
 |-20k-> L1-B
 |-20k-> L1-C
 |-20k-> L1-D
 `-20k-> L1-E

L1-A graduates -> HW stops feeding A
L1-A grows above protected floor
 |-20k-> L2-A1
 `-20k-> L2-A2 on later pulses
```

---

# 4. Round-trip cargo circulation

Population freighters no longer become permanently idle after delivering cargo.

After the validated Transport task unloads at the receiver, an empty idle freighter is evaluated for return/repositioning.

Return-source scoring uses:

- downstream population backlog;
- sustainable 20k-pulse production rate;
- exporter ring;
- travel distance;
- number of empty ships already staged at that exporter.

If an empty ship is already at a designated exporter, it stays there and waits when another hull already consumed that source's one allowed pulse this turn.

Otherwise it returns/repositions to the best active exporter.

### Stargate rule

Loaded cargo is never credited with stargate movement.

```text
LOADED ship -> must fly
UNLOAD cargo
EMPTY ship -> may eventually use a gate
```

The current native return implementation conservatively **flies** the empty return leg because generalized gate fleet orders are not yet a validated native primitive. Strategy keeps the empty-only gate rule so gate movement can be added later without redesigning logistics.

---

# 5. Small population fleet vs Large Freighter fleet

Population logistics and industrial bulk logistics are now separate capabilities.

## Population circulation

Preferred opening assets:

```text
Onion Privateer
Medium Freighter fallback
```

Production sizes this fleet from:

```text
sum of sustainable 20k pulses / turn
x
average loaded-out + empty-return cycle time
```

rather than raw population backlog.

Opening caps remain deliberately compact:

```text
through T20: <= 4 population freighters
toward T30: <= 5
later:       <= 8
```

This prevents Production from building a parking lot of transports when breeder growth only supports a few departures.

## Industrial bulk freight

Large Freighters are no longer treated as the answer to ordinary population demand.

They gain strong value when there is real bulk industrial concentration work, especially:

- mineral-rich inner worlds;
- mineral-starved forward shipyards;
- active combat/fleet production queues;
- large I/B/G stockpile relocation;
- major base/fleet construction hubs.

The initial Large-Freighter value threshold is deliberately high (roughly 600 kT of transferable bulk mineral pressure, or lower when multiple active shipyards are simultaneously building).

So:

```text
population backlog alone -> NO automatic C8 / Large Freighter sprint
bulk shipyard mineral pressure -> Large Freighter becomes valuable
```

---

# 6. Onion Privateer — preferred opening transport design

This release adds a dedicated semantic/native design target:

```text
Privateer hull
+ one legal quality engine
+ three basic Fuel Tanks
```

For an IFE race with Propulsion 2, the preferred engine is:

```text
Fuel Mizer
```

Otherwise the synthesizer conservatively reuses a legal engine already demonstrated by one of the player's own designs rather than inventing an unavailable engine.

Stock Privateer properties used by the planner:

```text
cargo:       250 kT
base fuel:   650 mg
engine count: 1
```

Three basic Fuel Tanks add:

```text
3 x 250 mg = 750 mg
```

for approximately:

```text
1,400 mg total fuel
```

while retaining the 250-kT hull cargo capacity.

That is deliberately matched to the onion pulse:

```text
200 kT population
+ 50 kT spare capacity
```

The spare capacity can later support small mineral bootstrap loads when combined-cargo loading is explicitly planned.

This is intended to be the workhorse for:

- HW -> Layer-1 population pulses;
- Layer-1 -> Layer-2 population pulses;
- small Germanium/mineral bootstrap work;
- loaded outward flight + empty return circulation.

It reduces the strategic need to research Construction 8 just for population movement.

---

# 7. Ship design lifecycle — create, delete, then recreate after read-back

v8.6 keeps native ship creation **enabled**, but removes the ambiguous v8.5 `replace_ship_design` abstraction.

There are now three distinct semantic states:

```text
create_ship_design
    target slot must already be genuinely FREE

delete_ship_design
    target slot must contain an existing design
    live ships using slot == 0
    queued builds using slot == 0
    design total_remaining == 0

replace_ship_design
    atomic native operation is BLOCKED
```

When all 16 ship-design slots are occupied and a dead design is selected for recycling, replacement is deliberately two-turn:

```text
Turn N
    DELETE dead design only
    Type27 = 10 <slot>

Host

Turn N+1
    M-file must show slot is free
    CREATE new design in that free slot
```

This prevents a create transaction from being mixed with a destructive delete and gives us a native read-back checkpoint between them.

## Hard active-hull invariant

The planner checks live/queued usage, but the native writer independently checks it again immediately before emitting Type27 bytes. Even if planning is wrong, a design with any live ship or queued build is refused at the serializer boundary.

## Corrected free-slot CREATE transaction

The user-provided Stars!-generated `GAME.x2` contains a free-slot-4 Long Range Privateer creation. Decoded Type27 records are:

```text
staging control: 11 A4
final control:   11 A4
```

The staging record uses the base hull name:

```text
Privateer
```

while the populated final record uses:

```text
Long Range Privateer
```

v8.5 incorrectly generalized this as `11 A4` followed by `11 64`, and it used the custom final name in the staging body. v8.6 reproduces the observed slot-4 transaction byte-for-byte in its golden test. Other free slots still remain experimental until host/client validation proves the `A0|slot` generalization.

## Scout design suppression

Fuel Mizer is no longer treated as automatically superior. The AI now compares the candidate against the **best existing scout**, with aggressive Warp-7 range as a primary mission metric. A candidate that materially reduces W7 range is rejected even if it gains free-cruise capability. This suppresses the redundant Fuel-Mizer scout observed in the playtest when the existing Daddy Long Legs 7 scout was already better for the active scouting doctrine.

---

# 8. Population native status

The controlled Stars!-generated population load anchor remains:

```text
<raw fleet u16> 25 00 12 08 19
```

where `0x19 = 25 kT`.

This release emits the opening economic pulse as:

```text
200 kT -> final quantity byte 0xC8
```

The writer permits only:

```text
1..255 kT
```

and verifies fleet cargo capacity before emission.

This remains **PARTIAL / EXPERIMENTAL** because the 200-kT quantity is an extrapolation from the directly observed 25-kT form. The destination Transport task itself is already the known unload-all/load-optimal policy:

```text
Ironium    Unload All
Boranium   Unload All
Germanium  Unload All
Population Unload All
Fuel       Load Optimal
```

For the first real v8.6 benchmark, inspect the first 200-kT transfer carefully:

1. host accepts X;
2. source loses exactly ~20,000 colonists;
3. target gains exactly ~20,000 colonists;
4. original client opens normally;
5. freighter arrives empty/refueled according to the Transport task;
6. following turn sends/returns the freighter according to the round-trip planner.

If any of those fail, disable `transport_population` and keep the strategic planner advisory until another controlled native sample resolves the encoding.

---

# 9. Starbase proliferation remains part of the onion program

The cumulative package retains the competitive support-base milestone:

```text
~T10: 1 useful support hub
~T18: 2
~T25: 3+
~T30: usually 4–5 if empire size/economy supports it
```

Only bases capable of **both ship construction and refueling** count as expansion support hubs.

Orbital Fort does not count.

### ISB hard rule

Only Improved Starbases (`ISB`) races may use:

```text
Space Dock
Ultra Station
```

Non-ISB races must use their race-legal normal support-base path (for example an existing legal Space Station design).

The package updates the historical regression fixture that previously expected a Space Dock from an IFE-only race; the fixture now explicitly includes `ISB` when testing Space Dock design development.

---

# 10. Research changes

Opening research now treats the logistics chain as capabilities rather than field balancing.

Important opening logic:

- Fuel Mizer is highly valuable for IFE expansion mobility.
- Construction 4 is valuable because it unlocks Privateer and other frontier logistics; Space Dock is added to the package only for ISB races.
- Stargates are valued for network/empty-return mobility, never for loaded cargo.
- Construction 8 / Large Freighter is **not** automatically valued by population backlog.
- Large Freighter becomes a research target when bulk industrial mineral concentration justifies it.
- unrelated mature technologies normally wait until the expansion network is healthy or a threat creates a concrete need.

---

# 11. Important files in this cumulative package

New/major strategy files:

```text
src/stars_ai/expansion_network.py
src/stars_ai/expansion_research.py
src/stars_ai/logistics_capacity.py
src/stars_ai/population_redistribution.py
src/stars_ai/ship_design_synth.py
src/stars_ai/starbase_planner.py
src/stars_ai/objective_production.py
src/stars_ai/design_development.py
src/stars_ai/fleet_intent.py
src/stars_ai/v4_coordinator.py
```

Native support:

```text
src/stars_ai/native/design_change.py
src/stars_ai/native/population_transport.py
src/stars_ai/native_capabilities.py
APPLY_NATIVE_WRITER_PATCH.py
```

Current-main colony integration:

```text
APPLY_COLONY_LAYER1_PATCH.py
```

Regression tests include:

```text
tests/test_expansion_network_v82.py
tests/test_expansion_research_v82.py
tests/test_expansion_research_v85.py
tests/test_logistics_capacity_v85.py
tests/test_population_redistribution_v83.py
tests/test_population_redistribution_v85.py
tests/test_objective_transport_v83.py
tests/test_objective_transport_v85.py
tests/test_privateer_onion_v85.py
tests/test_ship_design_v83.py
tests/test_ship_design_lifecycle_v86.py
tests/test_starbase_network_v83.py
```

plus corrected historical regression fixtures for the ISB and colony-population-unit rules.

---

# 11A. First native v8.6 validation

For the first retry, regenerate from a known-good pre-corruption M/X template. Turn 1 should isolate free-slot design creation naturally because the 100k economic population-export trigger normally has not fired yet.

For the first Type27 event, run the included pre-host verifier before hosting:

```powershell
python .\VERIFY_TYPE27_V86.py .\path\to\GAME.x1
```

Then verify, in order:

```text
1. pre-host X structurally decodes
2. Type27 create targets a free slot
3. staging/final controls are both 11 A0|slot
4. host processes the turn
5. next M-file contains the new design in that slot
6. original Stars! client opens the result
```

If the AI ever selects a dead-slot recycle, expect **only** `10 <slot>` on that turn. The replacement design must not be created until a later M-file confirms the deletion.

---

# 12. What to watch in the benchmark

The most informative observer checkpoints are T10, T20, T25, and T30.

Track at least:

```text
owned planets
population
Layer-1 designated hubs
Layer-1 graduated hubs
support starbases
population freighters
Onion Privateers
population shipments
unique exporters
empty-return orders
population sitting on HW
population in Layer-1 / Layer-2
mineral stockpiles at forward shipyards
Large Freighters
research tech vector
score vs classic AI
```

Desired qualitative behavior by T25–30:

```text
HW no longer supports every colony directly
4–5 useful Layer-1 hubs where map quality permits
several Layer-1 hubs at/above ~25% with support bases
those hubs feed Layer-2
small population freighters are continuously cycling
no exporter is stripped by two simultaneous 20k loads
Large Freighters appear only when industrial bulk logistics justify them
3–5 useful support bases when empire size supports it
```

That is the benchmark behavior this version is designed to test.

## Patcher hotfix

This package includes the corrected Layer-1 colony patcher. The original v8.5 release required a duplicated explanation anchor to appear once; current main correctly contains it twice (universal-hab and normal-hab branches). The corrected patcher requires exactly two and patches both.
