
# Stars! AI v7.1 — Exploration Routing

v7.1 is the exploration-routing release. It builds on v7.0 persistent strategic
memory and addresses the next failure seen in long autoplay tests: exploration
starts strongly, then stalls because probes use annual nearest-target decisions,
burn too much fuel at high warp, and repeatedly make expensive refueling detours.

No new unvalidated native Stars! order primitive is introduced by this release.

## 1. Persistent forward probe campaigns

Dedicated scouts are no longer assigned only one unrelated destination per year.

Each probe may receive a persistent route of up to 12 unknown planets:

```text
Scout #4
  P103 -> P118 -> P124 -> P147 -> ... -> P190
```

Only the next leg is emitted to the native X file. The remaining route persists
in the per-player memory file across turns.

Route state records:

- fleet id;
- remaining planet ids;
- expected discoveries;
- total planned distance;
- geographic sector;
- terminal / one-way status;
- whether a strategic refuel is pending.

A route is pruned as planets become observed or the scout reaches them.

## 2. Route objective: unique discoveries over probe lifetime

The route planner prioritizes:

- number of nearby unknown worlds that can be chained;
- continued outward movement from the empire;
- geographic separation from other scouts;
- shorter useful legs;
- fuel-feasible one-way travel.

The dominant objective is no longer "nearest unknown this turn."

The planner can reserve up to 12 future targets for a probe so another scout
does not independently converge on the same chain.

## 3. Geographic scout sectors

Dedicated probes receive stable angular sectors based on scout fleet id/order.

Sector fit is part of route scoring, encouraging scouts to spread outward in
different directions rather than repeatedly crossing each other's search area.

This is deliberately a soft constraint: if a sector lacks reachable unknown
worlds, the probe can still use useful targets outside it.

## 4. Reconnaissance is one-way by default

`scan` and `recon` missions no longer reserve fuel for a return trip.

The old behavior could reject a nearby unknown world because the scout could
not both reach it and preserve a homeward reserve, then send the scout hundreds
of light-years back to a base.

v7.1 defines probes as expendable sensors:

```text
continue discovering worlds
    >
return home merely to preserve the scout
```

Return/refuel is exceptional and must have a clear future exploration payoff.

## 5. Fuel Mizer / free-cruise behavior

The fuel planner now derives the highest zero-fuel warp from the actual engine
groups in the fleet.

For Fuel Mizer:

```text
Warp 1 = zero fuel
Warp 2 = zero fuel
Warp 3 = zero fuel
Warp 4 = zero fuel
```

Therefore a Fuel Mizer probe with effectively no fuel can still continue
exploration indefinitely at Warp 4.

Routine policy:

```text
healthy Fuel Mizer probe:
    Warp 5 when the leg is affordable

low/empty Fuel Mizer probe:
    Warp 4 free cruise

automatic return for fuel:
    NO
```

The implementation is engine-data based, so later scoop engines can expose
their own zero-fuel cruise capability.

## 6. Efficient conventional scout warp

The prior planner frequently selected Warp 9 because it chose the fastest
fuel-safe speed for a single leg.

For reconnaissance, conventional probes now normally cap routine cruise at
Warp 7. This intentionally trades some one-year speed for dramatically better
fuel lifetime and more total discoveries.

Combat, transport, colonization, and other mission fuel policies are unchanged.

## 7. Strategic refueling only

A dedicated scout may return to a refueling base only when all of these are true:

- no useful one-way route is currently available;
- the scout does not already have free Warp-4-or-better cruise;
- the base is at most 150 ly away;
- the base itself is fuel-reachable;
- refueling unlocks at least 5 planned unknown worlds.

The refuel route is scored by expected discoveries per campaign turn.

This replaces "low fuel => return to nearest starbase."

## 8. Milestone-driven scout production

The v7.0 strategic watchdog now drives actual scout fleet demand.

Scout demand considers:

- next exploration milestone;
- worlds still needed to reach the optimal milestone;
- turns remaining;
- measured discoveries over the last five turns;
- active/queued scout count;
- strategic exploration pressure.

The old static 3–4 scout behavior is removed.

The target force may grow to as many as 12 scouts when a large galaxy is far
behind its exploration schedule.

Under high exploration pressure, scout construction priority rises above
ordinary infrastructure work.

## 9. Scout design selection favors sustainable exploration

Among already-existing native scout designs, production preference now favors:

1. highest zero-fuel cruise warp;
2. efficient Warp-7 range;
3. efficient Warp-6 range;
4. fuel capacity;
5. lower dry mass.

This means an available Fuel Mizer scout can beat a conventional design even
if the latter looks attractive under a maximum-warp range calculation.

Brand-new native design creation remains blocked until safely validated.

## 10. Colony-ship overproduction throttled

The previous objective planner could create many empty colony ships simply
because many viable planets were known.

v7.1 bounds colony-fleet demand by:

- number of known viable claims;
- actual 25k population packets that mature owned worlds can export;
- already-loaded colony ships;
- a small one-hull pipeline allowance;
- phase-dependent concurrency cap.

This is intended to shift early shipyard capacity toward exploration when the
empire has more colony hulls than population available to launch them.

## 11. Fleet-intent fallback no longer defeats the route planner

If the persistent exploration router deliberately finds no useful forward route,
`fleet_intent.py` no longer immediately applies a naive nearest-unknown fallback.

The scout instead reports:

```text
HOLD / NO FORWARD ROUTE
```

with the reason that no one-way campaign or worthwhile refuel route is available.

This prevents a lower-level safety invariant from undoing higher-level route
reasoning.

## 12. Diagnostics

Recon movement payloads now include useful route information:

```text
route_managed
route_remaining
route_planet_ids
route_expected_discoveries
route_terminal
free_cruise_warp
exploration_pressure
```

Fuel diagnostics for a scout report:

```text
policy = one_way_probe
selected_warp
estimated_fuel
free_cruise_warp
return_reserve = 0
```

Strategic refuel orders report the route they are intended to unlock.

## Validation

v7.1 adds regression coverage for:

- Fuel Mizer zero-fuel Warp 4;
- no automatic refuel detour for a zero-fuel Fuel Mizer scout;
- 12-planet unique forward route generation;
- persistence/reuse of an existing multi-turn route;
- milestone deficit increasing scout force above the old six-scout ceiling;
- colony production bounded by exportable population;
- route diagnostics in emitted semantic scan orders.

Full release gate:

```text
237 passed
```

The next required validation remains a real Windows Stars! autoplay run, because
Python regression tests cannot prove native host stability or strategic quality.
