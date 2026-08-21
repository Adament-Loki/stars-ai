"""Fleet-level transport scheduling.

The native game gives a fleet one immediate cargo operation.  Population and
mineral planners therefore cannot independently claim the same hull.  This
module builds one common, continuously re-ranked list of *planet needs* and
assigns a freighter to the best remaining need.  A population delivery keeps
its first claim on the hold, then fills every legal remaining kT with minerals
needed by that same destination.

The ordering is deliberately destination-led: HW, P1, P2, then local worlds,
with tactical and blocked-base pressure layered on top.  Cargo type is a
property of the selected need rather than the first global tie breaker.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from .cargo_planner import derive_cargo_plan
from .expansion_network import evaluate_expansion_network
from .fuel_planner import mission_reachable_with_planned_cargo
from .logistics_capacity import export_source_statuses
from .planet_economy import decode_race_economy
from .population_redistribution import COLONISTS_PER_KT, _planet_under_fleet
from .util import distance
from .warp_policy import mission_warp

_MINERALS = ("ironium", "boranium", "germanium")


def _empty_load() -> dict[str, int]:
    return {mineral: 0 for mineral in _MINERALS}


def _cargo_mass(fleet: Any) -> int:
    cargo = (getattr(fleet, "native", {}) or {}).get("cargo", {}) or {}
    return sum(int(cargo.get(key, 0) or 0) for key in (*_MINERALS, "population"))


def _fleet_capacity(fleet: Any) -> int:
    return max(0, int(
        getattr(fleet, "cargo_capacity", 0)
        or (getattr(fleet, "native", {}) or {}).get("cargo_capacity", 0)
        or 0
    ))


def _tactical_value(planet: Any) -> float:
    native = getattr(planet, "native", {}) or {}
    values = (
        native.get("tactical_value"),
        native.get("strategic_value"),
        native.get("military_value"),
        native.get("frontier_value"),
    )
    for value in values:
        if value is not None:
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                continue
    return 0.0


def _destination_priority(planet: Any, hub: Any | None, base_demand: Any | None) -> float:
    """Stable strategic rank shared by population and freight demand.

    Tier gaps are intentionally larger than ordinary cargo quantities so a P1
    world with a routine shortage is normally served before a P2 world.  A
    blocked, strategic base and explicit tactical value may legitimately lift a
    world above that default sequence.
    """
    tier = int(getattr(hub, "promotion_tier", 3) or 3)
    # These bands intentionally dominate ordinary cargo quantities. A routine
    # P1 need is therefore served ahead of even a very hungry P2; an explicit
    # blocked-base/tactical condition is the deliberate way to override that
    # onion sequence.
    tier_score = {0: 4_000.0, 1: 3_000.0, 2: 2_000.0}.get(tier, 1_000.0)
    overall = max(0.0, float(getattr(hub, "overall_value", 0.0) or 0.0))
    strategic = max(0.0, float(getattr(hub, "strategic_value", 0.0) or 0.0))
    score = tier_score + 220.0 * overall + 300.0 * strategic + 1_000.0 * _tactical_value(planet)
    if base_demand is not None and not bool(getattr(base_demand, "ready", False)):
        deficit = sum(int(x or 0) for x in (getattr(base_demand, "mineral_deficit", {}) or {}).values())
        score += 1_500.0 + min(500.0, float(deficit))
    return score


def _order_priority(score: float) -> int:
    # Only controls native stream order; fleet assignment has already been
    # settled by the shared scheduler.  Keep it below diplomacy/design maxima.
    return max(110, min(185, int(round(105.0 + score / 22.0))))


def _population_options(
    state: Any,
    fleet: Any,
    source: Any,
    *,
    hubs: dict[int, Any],
    statuses: dict[int, Any],
    base_demands: dict[int, Any],
    population_inbound: dict[int, int],
    dispatched_sources: set[int],
) -> list[dict[str, Any]]:
    """Return feasible population needs for one fleet at its current source."""
    cap = _fleet_capacity(fleet)
    if cap <= 0:
        return []
    source_id = int(source.id)
    status = statuses.get(source_id)
    source_hub = hubs.get(source_id)
    if status is None or source_hub is None or not bool(status.ready_now):
        return []
    if source_id in dispatched_sources:
        return []

    out: list[dict[str, Any]] = []
    for target_hub in hubs.values():
        parent_id = getattr(target_hub, "parent_exporter_id", None)
        if parent_id is None or int(parent_id) != source_id:
            continue
        target = next((p for p in state.planets if int(p.id) == int(target_hub.planet_id)), None)
        if target is None or (target.habitability is not None and int(target.habitability) <= 0):
            continue
        remaining_colonists = max(
            0,
            int(getattr(target_hub, "import_population_to_25", 0) or 0)
            - int(population_inbound.get(int(target.id), 0)),
        )
        export_colonists = min(
            int(status.max_export_colonists),
            int(status.exportable_population),
            remaining_colonists,
            cap * COLONISTS_PER_KT,
        )
        export_colonists = (export_colonists // COLONISTS_PER_KT) * COLONISTS_PER_KT
        population_kt = export_colonists // COLONISTS_PER_KT
        if population_kt <= 0:
            continue
        if int(source.population or 0) - export_colonists < int(status.protected_floor):
            continue
        if not mission_reachable_with_planned_cargo(
            fleet, target.position, "transport", {"population": population_kt}
        ):
            continue
        # Population need is measured in colonists, so a P1 bootstrap backlog
        # must be allowed to outrank a routine mineral reserve at that same
        # world.  A strategically blocked base can still outrank it through
        # the destination-level base/tactical score.
        urgency = min(600.0, float(remaining_colonists) / 400.0)
        score = _destination_priority(target, target_hub, base_demands.get(int(target.id))) + urgency
        out.append({
            "kind": "population",
            "score": score,
            "source": source,
            "target": target,
            "source_hub": source_hub,
            "target_hub": target_hub,
            "status": status,
            "population_kt": population_kt,
            "population_colonists": export_colonists,
            "remaining_colonists": remaining_colonists,
        })
    return out


def _mineral_options(
    state: Any,
    fleet: Any,
    source: Any,
    *,
    owned: list[Any],
    hubs: dict[int, Any],
    economy: Any,
    orders: Any,
    base_demands: dict[int, Any],
    source_committed: dict[int, dict[str, int]],
    target_inbound: dict[int, dict[str, int]],
) -> list[dict[str, Any]]:
    """Return feasible mineral needs for one fleet, evaluated at current stock."""
    out: list[dict[str, Any]] = []
    for target in owned:
        if int(target.id) == int(source.id):
            continue
        base_demand = base_demands.get(int(target.id))
        cargo_plan = derive_cargo_plan(
            source,
            target,
            fleet,
            economy,
            orders,
            destination_minimum_stock=(
                getattr(base_demand, "mineral_required", None)
                if base_demand is not None else None
            ),
            source_committed=source_committed.get(int(source.id)),
            target_inbound=target_inbound.get(int(target.id)),
        )
        if cargo_plan is None:
            continue
        # ``derive_cargo_plan`` normally returns ``None`` for a zero payload,
        # but keep the scheduler defensive: a destination need is not enough
        # reason to burn a freighter turn unless this source can load cargo.
        if int(cargo_plan.total) <= 0:
            continue
        if not mission_reachable_with_planned_cargo(
            fleet, target.position, "transport", cargo_plan.as_load()
        ):
            continue
        hub = hubs.get(int(target.id))
        material_pressure = min(250.0, float(cargo_plan.total))
        germanium_pressure = min(120.0, float(cargo_plan.germanium) * 0.8)
        score = _destination_priority(target, hub, base_demand) + material_pressure + germanium_pressure
        out.append({
            "kind": "minerals",
            "score": score,
            "source": source,
            "target": target,
            "target_hub": hub,
            "base_demand": base_demand,
            "cargo_plan": cargo_plan,
        })
    return out


def _add_committed(destination: dict[int, dict[str, int]], planet_id: int, load: dict[str, int]) -> None:
    record = destination.setdefault(int(planet_id), _empty_load())
    for mineral in _MINERALS:
        record[mineral] += int(load.get(mineral, 0) or 0)


def _population_payload(
    state: Any,
    fleet: Any,
    option: dict[str, Any],
    mineral_load: dict[str, int],
    score: float,
) -> dict[str, Any]:
    source = option["source"]
    target = option["target"]
    status = option["status"]
    pop_kt = int(option["population_kt"])
    colonists = int(option["population_colonists"])
    capacity = _fleet_capacity(fleet)
    mixed = sum(mineral_load.values()) > 0
    baseline = pop_kt == 200 and not mixed
    experiment_id = (
        "population-type2-200kt-baseline"
        if baseline else f"population-type2-{pop_kt}kt" + ("-with-minerals" if mixed else "")
    )
    return {
        "fleet_id": int(fleet.id),
        "source_planet_id": int(source.id),
        "destination_planet_id": int(target.id),
        "population_kt": pop_kt,
        "population_colonists": colonists,
        "warp": int(mission_warp(fleet, target.position, "transport")),
        "score": round(float(score), 3),
        "source_ring": int(getattr(option["source_hub"], "ring", 0) or 0),
        "destination_ring": int(getattr(option["target_hub"], "ring", 0) or 0),
        "source_population_before": int(source.population or 0),
        "source_population_after": int(source.population or 0) - colonists,
        "source_protected_floor": int(status.protected_floor),
        "mineral_load": dict(mineral_load),
        "mission": "transport",
        "unload": {"ironium": "all", "boranium": "all", "germanium": "all", "population": "all"},
        "fuel": "load_optimal",
        "native_population_encoding": "type2_97_00_12_08_le16",
        "gate_allowed_while_loaded": False,
        "round_trip_logistics": True,
        "population_dispatch_policy": "one_capped_export_per_source_per_turn",
        "shared_logistics": {
            "need_rank": round(float(score), 3),
            "destination_priority": round(float(_destination_priority(target, option["target_hub"], None)), 3),
            "manifest_policy": "population need first; fill residual capacity with same-destination mineral need",
            "cargo_capacity": capacity,
            "manifest_total_kt": pop_kt + sum(int(v or 0) for v in mineral_load.values()),
        },
        "native_experiment": {
            "enabled": True,
            "id": experiment_id,
            "trust_level": "VALIDATED" if baseline else "EXPERIMENTAL",
            "validated_baseline": "200 kT Type-2 97 00 12 08 + LE16 quantity with 0x11 population transport task",
            "risk": (
                "None beyond the controlled population transport baseline."
                if baseline else "The Type-2 mixed-load field layout is client-captured; this selected quantity/mask remains trace-required until confirmed in a host turn."
            ),
            "mineral_capacity_kt": max(0, capacity - pop_kt),
            "shared_scheduler": True,
        },
    }


def _consolidate_bulk_lanes(state: Any, orders: Any, schedule: list[dict[str, Any]]) -> None:
    """Turn a population+freight multi-hull lane into one full one-turn fleet.

    ``sandbox/GAME.x1`` proves the client accepts Type37 merge followed by a
    mixed Type2 load and a waypoint in the same transaction.  We preserve that
    sequence whenever a population lane also needs another hull of minerals;
    a pure-mineral multi-hull lane remains separate until its Type2-only form
    has its own capture.
    """
    fleets = {int(f.id): f for f in state.fleets if f.owner == state.player_id}
    lanes: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for assignment in schedule:
        if assignment.get("kind") not in {"population", "minerals"}:
            continue
        source_id = assignment.get("source_planet_id")
        target_id = assignment.get("destination_planet_id")
        if source_id is None or target_id is None:
            continue
        lanes.setdefault((int(source_id), int(target_id)), []).append(assignment)

    replaced_fleet_ids: set[int] = set()
    merged_operations: list[tuple[int, dict[str, Any], dict[str, Any], str]] = []
    for (source_id, target_id), assignments in sorted(lanes.items()):
        if len(assignments) < 2:
            continue
        fleet_ids = sorted({int(a["fleet_id"]) for a in assignments})
        lane_fleets = [fleets.get(fid) for fid in fleet_ids]
        if any(f is None or _cargo_mass(f) != 0 or f.destination_planet_id is not None for f in lane_fleets):
            continue
        total_load = sum(int(a.get("manifest_total_kt", 0) or 0) for a in assignments)
        largest_capacity = max(_fleet_capacity(f) for f in lane_fleets)
        if total_load <= largest_capacity:
            continue
        population_assignment=next((a for a in assignments if a.get("kind") == "population"), None)
        if population_assignment is None:
            continue
        population_order=next((
            order for order in orders.orders
            if order.kind == "transport_population"
            and int(order.payload.get("fleet_id", -1)) == int(population_assignment["fleet_id"])
        ), None)
        if population_order is None:
            continue
        target_fleet_id = fleet_ids[0]
        source_fleet_ids = fleet_ids[1:]
        if not source_fleet_ids:
            continue
        need_rank = max(float(a.get("need_rank", 0.0) or 0.0) for a in assignments)
        merged_capacity = sum(_fleet_capacity(f) for f in lane_fleets)
        merge_payload = {
            "fleet_id": target_fleet_id,
            "target_fleet_id": target_fleet_id,
            "source_fleet_ids": source_fleet_ids,
            "source_planet_id": source_id,
            "planned_destination_planet_id": target_id,
            "planned_lane_load_kt": total_load,
            "projected_cargo_capacity_kt": merged_capacity,
            "mission": "merge_for_shared_transport",
            "one_turn_transport": True,
            "native_reference": "sandbox/GAME.x1: Type37 target + source fleet, immediately followed by a Type2 mixed load and Type4 waypoint",
            "native_experiment": {
                "enabled": True,
                "trust_level": "CLIENT_CAPTURED_LAYOUT",
                "id": "type37-then-type2-mixed-load-then-waypoint",
                "risk": "The exact Type37 + Type2 + Type4 layout is client-captured; the AI's selected quantities and added Type5 unload/refuel task remain trace-required.",
            },
        }
        combined_minerals=_empty_load()
        for assignment in assignments:
            order=next((
                candidate for candidate in orders.orders
                if int(candidate.payload.get("fleet_id", -1)) == int(assignment["fleet_id"])
                and candidate.kind in {"transport_population", "transport_minerals"}
            ), None)
            load=(
                order.payload.get("mineral_load", {})
                if order is not None and order.kind == "transport_population"
                else order.payload.get("load", {}) if order is not None else assignment.get("mineral_load", {})
            )
            for mineral in _MINERALS:
                combined_minerals[mineral] += int((load or {}).get(mineral, 0) or 0)
        combined_payload=dict(population_order.payload)
        combined_payload.update({
            "fleet_id": target_fleet_id,
            "mineral_load": combined_minerals,
            "merged_cargo_capacity_kt": merged_capacity,
            "native_population_encoding": "type2_97_00_12_mask_selected_le16",
        })
        combined_payload["shared_logistics"]={
            **dict(combined_payload.get("shared_logistics") or {}),
            "manifest_policy": "client-captured Type37 merge then one Type2 mixed manifest",
            "cargo_capacity": merged_capacity,
            "manifest_total_kt": total_load,
            "merged_fleet_ids": fleet_ids,
        }
        combined_payload["native_experiment"]={
            **dict(combined_payload.get("native_experiment") or {}),
            "id": "type37-then-type2-mixed-population-minerals",
            "trust_level": "CLIENT_CAPTURED_LAYOUT",
            "merged_fleet_capacity_kt": merged_capacity,
            "client_capture": "sandbox/GAME.x1: Type37 07 00 08 00 + Type2 07 00 97 00 12 0E 46 00 64 00 4A 01 + Type4 waypoint",
        }
        merged_operations.append((
            _order_priority(need_rank) + 1, merge_payload, combined_payload,
            (
                f"Shared transport lane {source_id}->{target_id} needs {total_load}kT, above any one "
                f"available hull ({largest_capacity}kT). Merge fleets {fleet_ids} at the source into "
                f"fleet {target_fleet_id} ({merged_capacity}kT), then load its combined colonist/mineral "
                "manifest and set the destination Transport unload/refuel task in this same X file."
            ),
        ))
        replaced_fleet_ids.update(fleet_ids)
        for assignment in assignments:
            assignment["status"] = "combined_after_fleet_merge"
            assignment["merge_target_fleet_id"] = target_fleet_id

    if not merged_operations:
        return
    orders.orders[:] = [
        order for order in orders.orders
        if not (
            order.kind in {"transport_population", "transport_minerals"}
            and int(order.payload.get("fleet_id", -1)) in replaced_fleet_ids
        )
    ]
    for priority, merge_payload, transport_payload, reason in merged_operations:
        orders.add("merge_fleets", merge_payload, reason, priority=priority)
        orders.add("transport_population", transport_payload, reason, priority=priority-1)


def schedule_shared_transport_orders(
    state: Any,
    orders: Any,
    plan: Any | None = None,
    *,
    support_base_material_demands: dict[int, Any] | None = None,
) -> list[dict[str, Any]]:
    """Assign each idle freighter exactly one highest-ranked transport need.

    The state-native schedule is intentionally verbose: it is the audit trail
    for why a fleet was allocated to a P1/P2/tactical world and how the final
    combined manifest was formed.
    """
    owned = [p for p in state.planets if p.owner == state.player_id]
    if not owned:
        return []
    base_demands = {int(k): v for k, v in (support_base_material_demands or {}).items()}
    network = evaluate_expansion_network(state)
    hubs = {int(h.planet_id): h for h in network.hubs}
    statuses = {int(s.planet_id): s for s in export_source_statuses(state, network)}
    economy = decode_race_economy(state.race)
    modifier = float(plan.objective("logistics") * plan.mission("transport")) if plan else 1.0

    source_committed: dict[int, dict[str, int]] = {}
    target_inbound: dict[int, dict[str, int]] = {}
    population_inbound: dict[int, int] = {}
    dispatched_sources: set[int] = set()
    available: list[tuple[Any, Any]] = []
    schedule: list[dict[str, Any]] = []

    for fleet in state.fleets:
        if fleet.owner != state.player_id or fleet.role != "freighter" or fleet.destination_planet_id is not None:
            continue
        here = _planet_under_fleet(fleet, owned)
        if here is None:
            continue
        cargo = (fleet.native or {}).get("cargo", {}) or {}
        if _cargo_mass(fleet) > 0:
            orders.add(
                "transport_unload_remainder",
                {
                    "fleet_id": fleet.id,
                    "destination_planet_id": here.id,
                    "warp": int((fleet.native or {}).get("observed_warp", 1) or 1),
                    "mission": "transport_unload_all",
                    "cargo_before": {key: int(cargo.get(key, 0) or 0) for key in (*_MINERALS, "population")},
                    "unload": {"ironium": "all", "boranium": "all", "germanium": "all", "population": "all"},
                    "fuel": "load_optimal",
                },
                f"Finish delivery at {here.name} before assigning another transport need.",
                priority=180,
            )
            schedule.append({"fleet_id": int(fleet.id), "status": "unload_remainder", "source_planet_id": int(here.id)})
            continue
        if _fleet_capacity(fleet) > 0:
            available.append((fleet, here))

    # Select one fleet at a time. Every selected manifest changes mineral and
    # population availability before the next selection, which is the required
    # reprioritization step rather than two independent transport passes.
    while available:
        candidates: list[tuple[float, int, int, dict[str, Any], Any]] = []
        for index, (fleet, source) in enumerate(available):
            for option in _population_options(
                state, fleet, source, hubs=hubs, statuses=statuses,
                base_demands=base_demands, population_inbound=population_inbound,
                dispatched_sources=dispatched_sources,
            ):
                candidates.append((float(option["score"]), 1, -int(fleet.id), option, fleet))
            for option in _mineral_options(
                state, fleet, source, owned=owned, hubs=hubs, economy=economy,
                orders=orders, base_demands=base_demands,
                source_committed=source_committed, target_inbound=target_inbound,
            ):
                candidates.append((float(option["score"]), 0, -int(fleet.id), option, fleet))
        if not candidates:
            break
        # On an exact need score, population receives the spare cargo rule;
        # otherwise destination need score is the sole primary decision.
        _, _, _, selected, fleet = max(candidates, key=lambda row: row[:3])
        source = selected["source"]
        target = selected["target"]
        available = [(f, p) for f, p in available if int(f.id) != int(fleet.id)]
        score = float(selected["score"])

        if selected["kind"] == "population":
            pop_kt = int(selected["population_kt"])
            if pop_kt <= 0:
                # Options are expected to exclude this, but never turn a
                # malformed population need into an empty flight.
                continue
            residual = max(0, _fleet_capacity(fleet) - pop_kt)
            mineral_load = _empty_load()
            if residual:
                proxy = SimpleNamespace(cargo_capacity=residual, native={"cargo_capacity": residual})
                base_demand = base_demands.get(int(target.id))
                cargo_plan = derive_cargo_plan(
                    source, target, proxy, economy, orders,
                    destination_minimum_stock=(
                        getattr(base_demand, "mineral_required", None)
                        if base_demand is not None else None
                    ),
                    source_committed=source_committed.get(int(source.id)),
                    target_inbound=target_inbound.get(int(target.id)),
                )
                if cargo_plan is not None:
                    mineral_load = cargo_plan.as_load()
                    planned = {"population": pop_kt, **mineral_load}
                    if not mission_reachable_with_planned_cargo(fleet, target.position, "transport", planned):
                        mineral_load = _empty_load()
            payload = _population_payload(state, fleet, selected, mineral_load, score)
            orders.add(
                "transport_population",
                payload,
                (
                    f"Shared transport need #{len(schedule)+1}: {target.name} ranks {score:.1f}; "
                    f"{fleet.name} carries {selected['population_colonists']:,} colonists first, then "
                    f"I/B/G={mineral_load['ironium']}/{mineral_load['boranium']}/{mineral_load['germanium']}kT "
                    f"to fill {payload['shared_logistics']['manifest_total_kt']}/{_fleet_capacity(fleet)}kT."
                ),
                priority=int(_order_priority(score) * modifier),
            )
            population_inbound[int(target.id)] = population_inbound.get(int(target.id), 0) + int(selected["population_colonists"])
            dispatched_sources.add(int(source.id))
            _add_committed(source_committed, int(source.id), mineral_load)
            _add_committed(target_inbound, int(target.id), mineral_load)
            schedule.append({
                "fleet_id": int(fleet.id), "kind": "population", "need_rank": round(score, 3),
                "source_planet_id": int(source.id), "destination_planet_id": int(target.id),
                "population_kt": pop_kt, "mineral_load": dict(mineral_load),
                "manifest_total_kt": int(payload["shared_logistics"]["manifest_total_kt"]),
                "capacity_kt": _fleet_capacity(fleet),
            })
            continue

        cargo_plan = selected["cargo_plan"]
        if int(cargo_plan.total) <= 0:
            # See the defensive option filter above. This check keeps a later
            # planner change from producing a no-cargo native operation.
            continue
        load = cargo_plan.as_load()
        target_hub = selected.get("target_hub")
        base_demand = selected.get("base_demand")
        payload = {
            "fleet_id": fleet.id,
            "source_planet_id": source.id,
            "destination_planet_id": target.id,
            "warp": mission_warp(fleet, target.position, "transport"),
            "load": load,
            "load_total": cargo_plan.total,
            "cargo_capacity": cargo_plan.capacity,
            "cargo_capacity_confidence": (fleet.native or {}).get("cargo_capacity_confidence", "unknown"),
            "unload": {"ironium": "all", "boranium": "all", "germanium": "all", "population": "all"},
            "fuel": "load_optimal",
            "cargo_plan": cargo_plan.to_dict(),
            "target_promotion": {
                "tier": int(getattr(target_hub, "promotion_tier", 3) or 3),
                "rank": getattr(target_hub, "promotion_rank", None),
                "overall_value": getattr(target_hub, "overall_value", None),
            },
            "support_base_material_delivery": base_demand.to_dict() if base_demand is not None else None,
            "shared_logistics": {
                "need_rank": round(score, 3),
                "manifest_policy": "highest-ranked destination mineral need",
                "manifest_total_kt": int(cargo_plan.total),
                "cargo_capacity": int(cargo_plan.capacity),
            },
        }
        orders.add(
            "transport_minerals",
            payload,
            (
                f"Shared transport need #{len(schedule)+1}: {target.name} ranks {score:.1f}; "
                f"{fleet.name} carries I/B/G={load['ironium']}/{load['boranium']}/{load['germanium']}kT "
                f"({cargo_plan.total}/{cargo_plan.capacity}kT). " + " ".join(cargo_plan.rationale)
            ),
            priority=int(_order_priority(score) * modifier),
        )
        _add_committed(source_committed, int(source.id), load)
        _add_committed(target_inbound, int(target.id), load)
        schedule.append({
            "fleet_id": int(fleet.id), "kind": "minerals", "need_rank": round(score, 3),
            "source_planet_id": int(source.id), "destination_planet_id": int(target.id),
            "mineral_load": dict(load), "manifest_total_kt": int(cargo_plan.total),
            "capacity_kt": int(cargo_plan.capacity),
        })

    _consolidate_bulk_lanes(state, orders, schedule)
    state.native["shared_transport_schedule"] = {
        "policy": "rank destination need globally; recompute after each fleet; population payloads fill residual hold with same-destination minerals",
        "assignments": schedule,
        "unassigned_freighter_ids": sorted(int(f.id) for f, _ in available),
        "population_inbound_colonists": {str(k): int(v) for k, v in population_inbound.items()},
        "mineral_inbound_kt": {str(k): dict(v) for k, v in target_inbound.items()},
    }
    return schedule
