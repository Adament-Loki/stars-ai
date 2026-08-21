# AI Status

This document is the current strategic and native-integration reference for Stars! AI v8.8. It replaces the dated release notes as the single place to record implemented behavior, confidence boundaries, strategic decisions, and the next work to validate.

## Current state

The AI can plan a complete turn from normalized or native player state and the Windows controller can stage a multi-player game, generate supported native orders, host it, merge player knowledge into history, and archive every phase. The engine is deliberately conservative at the native boundary: it emits only order forms with an evidence-backed serializer and reports the rest rather than guessing.

Every native player turn now decodes its current type-45 score record (seat, rank, score, planet count, and technology total). A private-score M-file contributes only the AI's own score to its trend record. When the M-file contains multiple score records—the public-score form—the strategic watchdog compares the current score with the leader and raises expansion and exploration pressure for a material deficit. It never uses rival totals in a private-score game.

| Area | Status |
| --- | --- |
| Native state decoding and normalized-state bridge | Implemented |
| Strategic planning, memory, traces, and command outcome logging | Implemented |
| Native autoplay, seed/live validation, history merge, and turn archives | Implemented |
| Supported native movement, colonization, production, research, relations, and submit forms | Implemented with feature-specific safety gates |
| Stock hull/slot model, legality, mass/fuel, and race-aware role fitting | Implemented |
| Fresh native Type27 ship-design creation | Player 1 host-accepted; owner-aware Player 2 encoding enabled, pending its own replay |
| Exact Stars! combat simulation and all native order semantics | Not yet complete |

The historical v8.8 implementation record reported 385 passing tests, 4 skips, and 10 pre-existing strategy-assertion failures; no cleanup-only change should be interpreted as a new test result. The known failures concern Layer-1 colony scoring, support-base thresholds, and legacy expansion-research expectations.

## Latest 50-turn review (turn 50 archive)

The `latestdemo` archive completed 50 turns. The review found that support-base queues could remain first while their host lacked the real Space Station material bill, and that both AI empires accumulated unbuilt Frigate combat variants while producing only the Onion Privateer. The current planner response is:

- A support base is material-gated by its remaining stock hull plus fitted-component I/B/G bill and a normal working reserve. A blocked base does not occupy its production queue; it remains an explicit, high-priority freight destination. Freight accounting tracks committed source loads and inbound deliveries so multiple carriers do not spend the same minerals on paper.
- Every planet now receives either a useful production queue or an explicit empty/research queue. Mines and factories precede bases, ships, and optional `Max Terraform`; a named high-commitment research sprint is the only intended exception.
- Combat and escort share one military design family. Existing combat designs are now actually queued after foreign contact. Never-built superseded variants are deleted only when the native safety check confirms zero live hulls, zero queued builds, and zero remaining production; only one Type27 design mutation is emitted in a turn.
- The expansion-race response now deliberately targets a substantially larger force: early reconnaissance targets at least six scouts and scales toward twenty-four with the unobserved map, the colony pipeline uses every population-supported viable claim rather than stopping at a milestone, and visible contact targets at least eight modern combat/escort hulls and scales toward twenty-four with empire size. These are floors, not stop conditions. Empire ship requests are split across every operational shipyard instead of being appended to one primary-world queue. Existing role counts no longer hide a newer design generation: an upgraded scout, colonizer, freighter, or combat hull receives its own build target alongside useful older ships.
- The design planner creates a Large Freighter only when actual concentrated base/shipyard debt makes it useful, and creates a remote miner only when an observed target and a race-legal mining robot exist. If the robot is unavailable, research now requests the first legal robot from `UNEDITED.MOD` rather than writing an illegal design.
- A remote miner that has reached its observed target now receives the client-captured Remote Mining waypoint task: Type 5 changes its sole stationary current planet waypoint from task 0 to task 3 at warp 0. The writer deliberately will not guess at a move-and-mine form, mutate a multi-waypoint route, or alter any other existing task; every emission carries the captured-byte provenance in the native trace.
- A tiny foreign scout group still establishes contact and supports normal defensive production, but no longer triggers a research `MILITARY_EMERGENCY`. The emergency classifier requires a substantive fleet (more than three hulls or more than 100 mass) inside the defense radius.

## Strategic decisions

### Settlement and expansion

Colonization is race-aware and phase-aware. A target must first be feasible: current intelligence, fuel, distance, source population, cargo capacity, route safety, and support constraints are hard gates. A desirable planet never overrides an infeasible operation.

Opening selectivity is intentionally high:

| Turn | Ordinary habitability floor | Strategic intent |
| --- | --- | --- |
| 0–15 | 60% | Secure strong nearby worlds; lower values need exceptional minerals or frontier value. |
| 16–25 | 50% | Broaden the first expansion ring. |
| 26–40 | 35% | Add resource and bridge worlds. |
| 41–55 | 20% | Establish resource outposts when support is available. |
| 56+ | Any green world | Prioritize mineral and network value. |

Universal-habitability races remain resource-driven. Ranking includes race-adjusted present habitability, current-tech and eventual terraforming potential, mineral value, local frontier, home-region distance, and network support.

The opening program aims for roughly 15–22 owned planets by turn 25 when the map permits it. The preferred first ring is four or five strong Layer-1 hubs around the homeworld, generally about 65–190 ly away. This is a ranking benefit, not permission to settle a bad world. Colonizer routes reserve their targets so multiple fleets do not idle over the same claim.

Planet promotion is a shared economic program, not a persona rule. Every owned world receives an economic score (habitability, capacity, minerals, and practical industry), a strategic score (relay position, frontier reach, and support capability), and an overall rank. The homeworld designates up to five P1 worlds; each P1 ranks up to three nearby P2 candidates. P2 development and export lanes remain staged until their P1 has population and operational shipyard/refuel support, so investment proceeds HW → P1 → P2 rather than attempting all layers at once. The same map guides colonization, infrastructure, freight, starbase selection, and scouting.

Early colony launches can leave a source at 1,000 colonists when it has reached 3,500. After the first colony or turn 10, normal source reserves resume. Multiple loads from a world share one population budget. Fleet reachability and warp calculations include the planned cargo rather than calculating a route for an empty hull.

Terraforming is considered as a real expansion input: the planner values current, current-tech, and eventual habitability and queues native auto-terraforming where the current technology can make a useful change.

### Economy, infrastructure, and logistics

Production is population-, mineral-, and capability-aware. Every AI uses the same baseline economy policy: homeworlds and operational ship/refuel hubs retain I/B/G working reserves, build mines before optional production while under their operable mine cap, and use their observed mineral concentrations plus race mine setting to estimate annual extraction. Factories are limited by surface Germanium after the relevant reserve floor; freight uses that same reserve model, so it cannot drain a production hub to fund another world. Routine research cannot displace this infrastructure growth; only a named 25% sprint or military emergency may do so. Queue actions still protect critical colony ships and military builds.

Starbases are evaluated by actual capability. Orbital Forts are not assumed to refuel or build ships; a network gap or frontier can justify a single active fuel-hub project using the lightest proven operational base design. Population and mineral freight capacity are tracked separately: a population-loaded freighter is reserved from industrial logistics, and an outer-hub/base mineral deficit creates a dedicated freight build when no unreserved carrier remains. This works with the best currently available freighter hull, so the AI does not wait for a Large Freighter research unlock before it can supply a new onion-layer base. Transport planning handles source/destination capacity, population and mineral cargo, remaining-cargo lifecycle, multi-waypoint delivery, and fuel with the final load.

Scouts have persistent campaigns, geographic sectors, one-way discovery goals, deconfliction reservations, sustainable warp policies, refueling rules, and a follow-up check that requires new intelligence before marking a scan complete. Their route scorer favors compact, fuel-safe local chains while labeling unknown systems as P1, P2, P3, or P4 relay opportunities. It completes the P1 constellation first, then searches outward from ranked parents instead of jumping past nearby survey work. Foreign contact redirects part of the scout screen to named border and intelligence missions, but does not stop uncontested exploration or replacement production; the policy and mission IDs are recorded in the native planning trace. Every active native route is re-evaluated each turn for its fastest fuel-safe arrival speed (normally up to Warp 9). A fitted Fuel Mizer scout follows that arrival policy; only a scout without Fuel Mizer engines retains the economical exploration-cruise exception. Same-target, same-task speed refreshes use the native Type-5 waypoint form, while destination retargeting remains blocked. Fleet-intent rules prevent colony ships, cargo fleets, miners, minelayers, and scouts from silently idling.

### Research

Research chooses a named capability and its concrete use rather than simply flattening the six fields. The catalog covers freight hulls, support bases, IFE/Fuel Mizer, and known terraforming breakpoints; strategic subsystems may add demands.

Planner postures are `EXPANSION_FIRST`, `TARGETED`, `SPRINT`, `MILITARY_EMERGENCY`, `MATURE_SURGE`, and `RECOVERY`. Expansion debt discounts nonessential work, while a nearby threat can force military research. Persistent memory uses a material-challenger threshold to avoid goal oscillation and drops a stalled sprint back to a safer recovery posture.

Normal autonomous allocation uses the validated 15% native form; a one- to five-level high-value goal can use 25%. Research commands record the current/next field, percentage, goal, horizon, contributors, protected planets, and expected unlock action.

### Combat and military decisions

Military power is assessed by designs and fitted equipment, not raw fleet count. The evaluator accounts for beam and torpedo strength, shields, armor, accuracy, initiative, combat speed, range profile, cost, and relative technology. It distinguishes modern from obsolete strength and estimates trade quality.

The strategic outcomes are:

- `BUILD_CURRENT` — existing designs remain efficient.
- `FIGHT_NOW` — visible force quality and territorial value justify engagement.
- `TECH_THEN_REBUILD` — preserve the core, accept limited fringe loss, reach a material design upgrade, then re-enter combat.
- `HOLD_AND_TECH` — retain a defensible position while closing a research gap.
- `RETREAT_AND_PRESERVE` — do not trade irreplaceable fleets for low-value territory.

Core and high-sunk-cost worlds can override deliberate sacrifice and force emergency defense. The evaluator is doctrine-aware, not yet an exact Stars! battle-board simulator; battle mechanics and component statistics remain the major fidelity gap.

### Diplomacy, personas, and territorial value

Personas set macro priorities such as expansion pressure, research, aggression, and risk tolerance. Visible relations are reciprocal and human-player alliances have explicit safety rules. Territorial value includes investment, infrastructure, strategic position, and replacement cost, so the AI can distinguish a recoverable fringe world from a core loss.

## Native integration and safety boundary

The native core is modeled after StarsAPI structures and preserves unknown raw record data. The host controller checks game identity, seats, turns, headers, X template compatibility, and staged/live file consistency before any live mutation. It treats the seed as immutable and keeps persistent X templates because host consumption makes live X files disposable.

After a successful host, each current player `.m#` is merged into cumulative `.h#` history without modifying the M file. The merge is assembled and checked before atomic installation; semantic observation-turn checks stop the run if history coverage regresses. Each archive phase records file hashes and capture stability, with an optional JSON index.

Supported native operations are intentionally narrow. The writer uses empirical forms for route/waypoint changes, colonization loads and task assignment, production queues, research allocation, leftover-only production contribution, friend relations, and submission. Unsupported or unsafe candidates are skipped with a reason in the native report rather than serialized speculatively.

### Ship design creation

The ship builder now packages StarsAPI's unmodified `UNEDITED.MOD` as `data_unedited.mod`. Every candidate component is selected from the current six research levels, then checked against the official Stars! help-file PRT/LRT gates before slot legality is evaluated. This includes IFE Fuel Mizer/Galaxy Scoop, NRSE engine exclusions and Interspace-10, OBRM miners, NAS penetrating scanners, ARM robots, and PRT-specific equipment. A generic `create_design` proposal is no longer advisory-only when it describes a ship: it is compiled through this same gate into the existing free-slot Type27 lifecycle. Starbase design mutation remains advisory until a client capture validates its distinct lifecycle.

The direct result for the active IT+IFE race is a `Colonizer Mk II` with a Fuel Mizer and Colonization Module at Propulsion 2; the temporary client staging name remains the stock `Colony Ship` class, and the final record retains the strategic custom name.

The embedded Type27 design body must exactly round-trip through the StarsAPI-compatible codec. Deletion is allowed only for an empty, unqueued design slot; replacement is delete → read back on a later turn → create. A fresh create is isolated from unrelated orders and framed as:

```text
FileHash → Type27 staging → Type27 final → Footer
```

The Player 1 transaction is now host-validated: the isolated three-turn `type27-owner-p1` playtest emitted only the owner-correct pair on turn 3, the host consumed the X file and advanced 2402 → 2403, and the returned `GAME.m1` contains slot 4 `Onion Privateer` (Privateer, Fuel Mizer, three Fuel Tanks). A ship build and a dedicated Player 2 replay remain the next live gates.

The following records are the historical investigation that led to the corrected framing.

**Current gate (latestdemo, turn 3): failed.** The isolated Player 1 create order contains a legal IFE Fuel Mizer design, exact FileHash length, and a codec-round-tripping body, but the host leaves both player X files unconsumed and the game remains at 2402. The native HST/M/XY files are unchanged, so this is an order-registration rejection/stall rather than confirmed game-file corruption. Do not emit Type27 create/delete orders in routine play until a complete client-generated create transaction has been captured and reproduced through a host-accepted disposable replay.

The subsequent replay with the requested staging name (`SuperRabits Privateer`) fails in precisely the same place. The name change is present in the encoded staging record (and changes its length from 46 to 54 bytes), while the final custom name remains `Onion Privateer`. The later human control establishes that the race-prefixed staging name is noncanonical, although the leading Type46 framing error is the stronger rejection candidate.

**Human control captured (sandbox `GAME.x2`, turn 2402):** a manually created `TestP` Privateer with only one Fuel Mizer uses the same `11 A4` staging and `11 64` final controls, the same full-design header, and empty optional slots. Its complete order stream is `FileHash → ordinary orders → Type27 staging → Type27 final → one Type46 SaveAndSubmit → Footer`. The failed AI stream instead used `FileHash → Type46 → Type27 staging → Type27 final → Type46 → Footer`. That leading Type46 “isolation sandwich” is the concrete host-rejection candidate. The writer now uses the client order and stages with the stock class name `Privateer`; a disposable host replay remains required before routine Type27 emission is enabled. The human FileHash length is correct for its longer, combined stream, so FileHash accounting is not implicated.

**Revised replay (latestdemo, turn 3): failed again, but the correction was applied.** The pre-host audit records `FileHash=105`, then exactly Type27 staging (`Privateer`, 46 bytes), Type27 final (Fuel Mizer plus three Fuel Tanks, 49 bytes), and one final Type46. The host leaves the X files unconsumed and does not change HST/M/XY; its output is only `version: 2446`, and the runner exits without a post-host or timeout audit. The remaining non-client-validated difference was `type27_isolation=true`: Player 1 intentionally skipped all normal order blocks, while the manually created client X2 appended its Type27 pair after ordinary planet/research/production orders. The writer now retains those normal orders, appends the Type27 pair, and uses exactly one final Type46; a fresh disposable host replay is required.

**Combined replay (latestdemo, turn 3): failed again; revised encoder confirmed active.** The audit records `FileHash=210` and the exact block sequence `PlanetChange, ResearchChange, ProductionQueueChange, ProductionQueueChange, ManualSmallLoadUnloadTask, WaypointAdd, WaypointChangeTask, ProductionQueueChange, Type27 staging, Type27 final, Type46`. HST/M/XY remain unchanged. This is not stale code, but it is not a clean Type27 conclusion either: the manual client design control had only the first three ordinary block families, while this test introduced a new multi-mineral transport/waypoint transaction (`Type1/4/5`). The writer now limits the next Type27 compatibility probe to Player 1's Planet/Research/Production blocks, followed by Type27 and one final Type46; transport and movement are deliberately suppressed for that test.

**Narrowed replay (latestdemo, turn 3): failed again.** The audit records `FileHash=161` and `PlanetChange, ResearchChange, ProductionQueueChange, ProductionQueueChange, ProductionQueueChange, Type27 staging, Type27 final, Type46`; there are no Type1/4/5 transport or waypoint blocks. That rules out the surrounding transport/movement hypothesis. The final remaining difference from the human client control is the design body: the AI emits `Onion Privateer` with three Fuel Tanks, while the user-created control is engine-only `TestP`. The next compatibility probe now emits that exact human-captured TestP Privateer body byte-for-byte, while retaining Player 1's header, slot, turn, and client-shaped order stream.

**Exact-TestP replay (latestdemo, turn 3): failed again.** The AI matched the human design body (`Privateer` staging and engine-only `TestP` final) while retaining only Planet/Research/Production orders beforehand, but it retained a historical `11 64` final wrapper and therefore was not yet an exact transaction match. The host left X/HST/M/XY unchanged.

**Fresh client H2/X2 capture (preserved at `playtests/evidence/manual-testp-turn2402-20260820`):** the current manual client transaction is `11 A4` staging followed by `11 A4` final—not the historical A4/64 variant. The H2 companion grows from 164 to 190 bytes by appending a 24-byte PlayerScores block; it does not contain a design record. The live encoder now defaults to this freshly observed A4/A4 form, while explicit fixture tests preserve the older A4/64 evidence. Do not infer or write H-file changes until an A4/A4 host replay proves they are required.

The failed AI design also was not an exact replay of the captured client Privateer: the client reference includes two ordinary shields in the shield/armor slot, while the AI wrote that slot empty. Fuel capacity, shield DP, mass, and resource cost are derived by the client from hull and slot components; only the aggregate armor field is carried directly in a full DesignBlock. Empty optional slots are normally legal, so this difference is a missing control variable rather than a demonstrated rejection cause.

**Encoding gap found during review:** the current ship synthesizer writes hull base armor but does not add the armor supplied by fitted armor components (or the special armor-bearing shields/cargo pod). StarsAPI adds those values to the full DesignBlock armor field. The latest Fuel-Mizer Privateer is unaffected because it has no such component; any generated armor-bearing design remains unsafe until this is corrected and host-validated.

### Player 1 Type27 client control (turn 2402, host-accepted)

The client-authored Player 1 capture is in `playtests/runs/latestdemo/logs/turn-archive/turn-003/00-pre-write/game` and is preserved at `playtests/evidence/p1-type-longhump6-turn2402-20260820`. The failed AI transaction remains unchanged in the matching `10-pre-host/game` archive. `GAME.hst`, `GAME.xy`, `GAME.m1`, `GAME.m2`, `GAME.m3`, `GAME.h2`, and `GAME.h3` are byte-identical between the two snapshots, so every difference below is part of the Player 1 client transaction rather than a different game state.

| Area | Client P1 control | Failed AI P1 file |
| --- | --- | --- |
| H1 size and header | 192 bytes; turn 2, salt 1580, flags `E1` | 166 bytes; turn 0, salt 168, flags `01` |
| H1 counters | `05 00 06 00` | `05 00 05 00` |
| H1 planet/filter records | Five Type14 partial planets and the 49-byte Type33 filter; each is byte-identical | Same five Type14 records and Type33 filter |
| H1 PlayerScores | Includes Type45, 24 bytes: `20 80 02 00 22 00 00 00 85 00 00 00 03 00 01 00 05 00 02 00 00 00 0E 00` | Absent |
| X1 header | Turn 2, player 1, salt 1242, type 1, flags `E1` | Turn 2, player 1, salt 1066, type 1, flags `E1` |
| FileHash | Order length 94; canonical 15-byte tail unchanged | Order length 156; same canonical tail |
| Order stream | Type27 staging, Type27 final, footer | PlanetChange, ResearchChange, three ProductionQueueChange blocks, Type27 staging, Type27 final, Type46 SaveAndSubmit, footer |
| Type27 wrapper | Both records begin `01 A4` | Both records begin `11 A4` |
| Staging design body | Slot 4, Privateer (hull 11/picture 44), armor 150, turn 2, five empty slots, name `Privateer` | Byte-identical embedded design body; only wrapper differs |
| Final design body | Slot 4, same hull/picture/armor/turn and empty optional slots; engine `(category 1, item 3, count 1)` = Long Hump 6; raw name bytes `04 C3 E2 DC 2F`, decoded/stored as `Type` | Engine `(1, 2, 1)` = Fuel Mizer; raw name bytes `04 C3 29 AB FF`, decoded as `TestP` |
| Type46 | None | One `01 01 05 19` block |

The client control was replayed against a disposable copy of this exact baseline. The host consumed X1, advanced the game to 2403, and added slot-4 `Type`: a Privateer with armor 150, one Long Hump 6, and four empty optional slots. This proves the P1 client transaction is valid and that the earlier AI file was rejected before design installation.

The wrapper's first byte now has a strong owner interpretation: the valid Player 1 capture uses `01` and the valid Player 2 capture uses `11`, exactly matching `((player_id - 1) << 4) | 0x01`. The second byte remains `A4` for ship-design slot 4. The failed AI Player 1 file incorrectly used the Player 2 form `11 A4`, which is the leading concrete rejection cause. This also matches the client's empty staging design: it identifies the owner and design slot so other players can retain an unknown-hull placeholder until their intelligence reveals the complete design.

Automatic Type27 creation is enabled only as the narrow client-shaped transaction: one mutation, owner-correct wrappers, the staging/final pair alone, and no Type46 or unrelated orders. The successful Player 1 replay created `Onion Privateer` with the legal IFE Fuel Mizer; Player 2 uses the corresponding `11 A4` owner wrapper so later turns do not serialize Player 1's form for Player 2. Player 2 creation and all delete forms still need their own host replays.

### Population transport: validated control and enabled experiments

The turn-8 stall was not a Type27 failure. The AI wrote a 200-kT population load as Type1 `03 00 25 00 12 08 C8`, extrapolating the one-byte 25-kT colony record. The human client instead writes Type2 `ManualMediumLoadUnloadTask`: `<fleet> 97 00 12 08 C8 00`, then a `0x11` waypoint/task pair with `00 00 00 00 00 00 00 20` (unload all population only). The human control hosts successfully. Replacing only that P1 triplet in the complete failed turn-8 P1/P2 transaction also hosts successfully.

The 200-kT population-only trip is the validated control. The AI now also enables two explicitly trace-required experiments: a positive little-endian u16 quantity in that same Type2 record (the early homeworld uses 80 kT / 8,000 colonists until it reaches 200,000 population), and a capacity-bounded Type1 I/B/G mineral load after the Type2 population load. Mixed cargo uses the independently observed `0x51` Transport endpoint with all cargo unload plus optimal fuel; population-only retains the controlled `0x11` endpoint. Every emitted experimental population action writes its experiment id, trust level, cargo, route/task policy, and exact Type1/Type2/Type4/Type5 hex blocks into the decision-native archive and human-readable decision report.

## Observability and regression protection

Decision traces expose candidates, scores, score factors, hard-rule disqualifications, selected action, and plain-language rationale. Command expectations are checked on later turns as `COMPLETED`, `PENDING`, `WARNING`, or `UNVERIFIED`. Native reports make emitted and skipped commands auditable. The test suite covers the strategy modules, binary records, order lifecycle, history merge, staging, archive integrity, and conservative design system.

## Next work

1. Run a dedicated Player 2 Type27 create replay, then queue and host-build the Player 1 `Onion Privateer` to complete the create-and-build gate.
2. Resolve the ten known legacy strategy-test failures or deliberately update their expectations with a documented strategic decision.
3. Port exact component statistics and battle-board mechanics for a true combat simulator.
4. Keep every implemented native capability enabled; run controlled client/host replays to promote its trace-labelled experimental variants to validated status.
5. Run reproducible 5-, 25-, and 50-turn playtests across multiple PRTs and personas, then tune colonization, hub, research, and combat thresholds from results.

When a strategic policy or native-format fact changes, update this file in the same change. Keep `README.md` focused on code, installation, execution, and testing.
