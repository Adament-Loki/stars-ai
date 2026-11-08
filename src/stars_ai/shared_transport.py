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
from .logistics_capacity import (
    evaluate_logistics_capacity,
    export_source_statuses,
    population_export_floor,
    population_transport_throughput,
)
from .native.population_transport import MAX_SMALL_POPULATION_LOAD_KT
from .planet_economy import decode_race_economy
from .invasion import weak_undefended_colony
from .population_invasion import is_recently_lost_world, population_capture_targets
from .population_redistribution import (
    COLONISTS_PER_KT,
    _planet_under_fleet,
    plan_empty_freighter_returns,
)
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
    network: Any,
    hubs: dict[int, Any],
    statuses: dict[int, Any],
    base_demands: dict[int, Any],
    population_inbound: dict[int, int],
    population_source_outbound: dict[int, int],
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
    already_dispatched = int(population_source_outbound.get(source_id, 0) or 0)

    out: list[dict[str, Any]] = []
    for target_hub in hubs.values():
        parent_id = getattr(target_hub, "parent_exporter_id", None)
        if parent_id is None or int(parent_id) != source_id:
            continue
        target = next((p for p in state.planets if int(p.id) == int(target_hub.planet_id)), None)
        if target is None or (target.habitability is not None and int(target.habitability) <= 0):
            continue
        from .planet_economy import may_receive_homeworld_population
        if not may_receive_homeworld_population(source, target, state.race):
            continue
        remaining_colonists = max(
            0,
            int(getattr(target_hub, "import_population_to_25", 0) or 0)
            - int(population_inbound.get(int(target.id), 0)),
        )
        source_available = min(
            max(0, int(status.exportable_population) - already_dispatched),
            max(0, int(source.population or 0) - int(status.protected_floor) - already_dispatched),
        )
        export_colonists = min(
            # Each departure is constrained by real source surplus, remaining
            # destination need, and the selected hull--not a packet policy.
            source_available,
            remaining_colonists,
            cap * COLONISTS_PER_KT,
        )
        export_colonists = (export_colonists // COLONISTS_PER_KT) * COLONISTS_PER_KT
        population_kt = export_colonists // COLONISTS_PER_KT
        if population_kt <= 0:
            continue
        if int(source.population or 0) - already_dispatched - export_colonists < int(status.protected_floor):
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
        # A world still under its uncrowded band outranks a mineral haul.
        # The 100-turn journals sent ironium while new colonies sat at 2,500 people.
        underfed = int(getattr(target, "population", 0) or 0) < int(getattr(target_hub, "capacity", 1) or 1) * 0.25
        score = _destination_priority(target, target_hub, base_demands.get(int(target.id))) + urgency
        if underfed:
            score += 1_200.0
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
            "source_already_dispatched": already_dispatched,
            "remaining_colonists": remaining_colonists,
        })
    return out


def _population_capture_options(
    state: Any,
    fleet: Any,
    source: Any,
    *,
    plan: Any | None,
    network: Any,
    hubs: dict[int, Any],
    dispatched_sources: set[int],
    reserved_target_ids: set[int],
) -> list[dict[str, Any]]:
    """Return one-turn, large-transport population captures from this source.

    The normal logistics program uses compact pulses.  Capture is intentionally
    a different, rare operation: it consumes a large empty hull, leaves the
    breeder above its normal protected floor, and only targets a small observed
    non-AR colony.  It never falls back to a guessed Type-25 encoding.
    """
    if int(source.id) in dispatched_sources:
        return []
    capacity = _fleet_capacity(fleet)
    source_hub = hubs.get(int(source.id))
    if source_hub is None:
        return []
    protected_floor = int(population_export_floor(state, source_hub, network))
    out: list[dict[str, Any]] = []
    for target_info in population_capture_targets(state):
        # Capture uses the same one-fleet/one-world reservation rule as
        # colony and scout work.  Excluding this candidate here (rather than
        # holding after its score wins) lets the next scheduler iteration
        # recompute and select the best remaining executable foreign world.
        if int(target_info.planet_id) in reserved_target_ids:
            continue
        if int(target_info.required_kt) > capacity:
            continue
        target = next((p for p in state.planets if int(p.id) == int(target_info.planet_id)), None)
        if target is None:
            continue
        recapture = is_recently_lost_world(target)
        if plan is not None and not recapture:
            diplomacy = getattr(plan, "diplomacy", {}) or {}
            view = diplomacy.get(int(target_info.owner_id), diplomacy.get(str(target_info.owner_id), {})) or {}
            attitude = str(view.get("attitude", "neutral") or "neutral").lower()
            action = str((view.get("conflict", {}) or {}).get("recommended_action", "coexist") or "coexist").lower()
            # An ordinary population capture is a war action.  Only the
            # immediate recovery of a world just taken from us may bypass the
            # normal hostile/opposed diplomacy gate.
            if attitude in {"helpful", "allied"}:
                continue
            # Neutral seats are still legal targets when the colony is small,
            # observed, and undefended. Allies and helpful partners are not.
            if attitude != "hostile" and action != "oppose" and not weak_undefended_colony(state, target):
                continue
        if int(source.population or 0) - int(target_info.required_colonists) < protected_floor:
            continue
        if not mission_reachable_with_planned_cargo(
            fleet, target.position, "transport", {"population": int(target_info.required_kt)}
        ):
            continue
        # Recovering a world captured this turn prevents a quick enemy snowball
        # and preserves infrastructure already paid for.  Other legal enemy
        # colonies remain valuable, but do not outrank that direct recovery.
        score = (
            (7_000.0 if recapture else 5_500.0)
            + min(250.0, float(getattr(target, "habitability", 0) or 0) * 2.0)
            - min(500.0, distance(source.position, target.position))
            - float(target_info.estimated_population) / 400.0
        )
        out.append({
            "kind": "population_capture",
            "score": score,
            "source": source,
            "target": target,
            "source_hub": source_hub,
            "target_info": target_info,
            "population_kt": int(target_info.required_kt),
            "population_colonists": int(target_info.required_colonists),
            "protected_floor": protected_floor,
            "recapture": recapture,
        })
    return out


def _population_capture_payload(state: Any, fleet: Any, option: dict[str, Any]) -> dict[str, Any]:
    source = option["source"]
    target = option["target"]
    target_info = option["target_info"]
    pop_kt = int(option["population_kt"])
    return {
        "fleet_id": int(fleet.id),
        "source_planet_id": int(source.id),
        "destination_planet_id": int(target.id),
        "population_kt": pop_kt,
        "population_colonists": int(option["population_colonists"]),
        "warp": int(mission_warp(fleet, target.position, "transport")),
        "score": round(float(option["score"]), 3),
        "source_population_before": int(source.population or 0),
        "source_population_after": int(source.population or 0) - int(option["population_colonists"]),
        "source_protected_floor": int(option["protected_floor"]),
        "mineral_load": _empty_load(),
        "mission": "transport",
        "unload": {"ironium": "all", "boranium": "all", "germanium": "all", "population": "all"},
        "fuel": "unchanged",
        "native_population_encoding": "type2_source_planet_u16_12_mask_selected_le16",
        "population_capture": {
            "operation": "counter_capture" if bool(option.get("recapture")) else "expansion_capture",
            "target_owner_id": int(target_info.owner_id),
            "target_population_estimate": int(target_info.estimated_population),
            "target_prt_id": target_info.target_prt_id,
            "target_prt_name": target_info.target_prt_name,
            "policy": "large-colonist capture; excluded when target is known Alternate Reality",
        },
        "shared_logistics": {
            "need_rank": round(float(option["score"]), 3),
            "manifest_policy": "dedicated large population capture",
            "cargo_capacity": _fleet_capacity(fleet),
            "manifest_total_kt": pop_kt,
        },
        "native_experiment": {
            "enabled": True,
            "id": f"population-capture-type2-{pop_kt}kt",
            "trust_level": "EXPERIMENTAL",
            "risk": "Type-2 population quantity is within its u16 field ceiling; the tactical capture remains trace-required until host-confirmed.",
            "type25_policy": "Not used: Type-2 has no format transition at 1,000 kT.",
        },
    }


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
    source_before = int(source.population or 0) - int(option.get("source_already_dispatched", 0) or 0)
    mixed = sum(mineral_load.values()) > 0
    baseline = pop_kt == 200 and not mixed
    baseline_80kt = pop_kt == 80 and not mixed
    small_population_only = 1 <= pop_kt <= MAX_SMALL_POPULATION_LOAD_KT and not mixed
    experiment_id = (
        "population-type2-200kt-baseline"
        if baseline else "population-type1-80kt-baseline"
        if baseline_80kt else f"population-{'type1' if small_population_only else 'type2'}-{pop_kt}kt" + ("-with-minerals" if mixed else "")
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
        "source_population_before": source_before,
        "source_population_after": source_before - colonists,
        "source_protected_floor": int(status.protected_floor),
        "mineral_load": dict(mineral_load),
        "mission": "transport",
        "unload": {"ironium": "all", "boranium": "all", "germanium": "all", "population": "all"},
        "fuel": "unchanged",
        "native_population_encoding": (
            "type1_source_planet_u16_12_08_u8"
            if small_population_only else "type2_source_planet_u16_12_mask_selected_le16"
        ),
        "gate_allowed_while_loaded": False,
        "round_trip_logistics": True,
        "population_dispatch_policy": "source_surplus_destination_need_and_hold_with_protected_floor",
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
            "trust_level": "VALIDATED" if baseline_80kt else "EXPERIMENTAL",
            "validated_baseline": (
                "80 kT Type-1 <source planet u16> 12 08 50 + 0x51 Transport unload-all I/B/G/population "
                "endpoint is host-validated end-to-end."
            ),
            "risk": (
                "None beyond the end-to-end validated 80 kT transport baseline."
                if baseline_80kt else "The Type-2 selected quantity/mask and its population-only endpoint pairing remain trace-required until confirmed in a host turn."
            ),
            "mineral_capacity_kt": max(0, capacity - pop_kt),
            "shared_scheduler": True,
        },
    }


def _consolidate_bulk_lanes(state: Any, orders: Any, schedule: list[dict[str, Any]]) -> None:
    """Turn a population+freight multi-hull lane into one full one-turn fleet.

    A native client capture proves the client accepts Type37 merge followed by a
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
        population_assignments=[a for a in assignments if a.get("kind") == "population"]
        # The client capture proves one population hull plus one mineral hull
        # can merge into a single mixed manifest.  It does not prove that a
        # batch of independent population exports may merge safely; preserve
        # those separate freighters so an overfull homeworld can feed several
        # hubs in parallel.
        if len(population_assignments) != 1:
            continue
        population_assignment=population_assignments[0]
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
            "native_reference": "native client capture: Type37 target + source fleet, immediately followed by a Type2 mixed load and Type4 waypoint",
            "native_experiment": {
                "enabled": True,
                "trust_level": "CLIENT_CAPTURED_LAYOUT",
                "id": "type37-then-type2-mixed-load-then-waypoint",
                "risk": "The exact Type37 + Type2 + Type4 layout is client-captured; the AI's selected quantities and Type5 unload-all-cargo task remain trace-required.",
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
            "native_population_encoding": "type2_source_planet_u16_12_mask_selected_le16",
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
            "client_capture": "latestdemo turn-022 P2: Type37 05 02 06 02 + Type2 05 02 <source planet 19> 12 0C FA 00 48 03 + Type4/Type5 Transport unload-all waypoint; Type37 retains the owner tag just like Type2.",
        }
        merged_operations.append((
            _order_priority(need_rank) + 1, merge_payload, combined_payload,
            (
                f"Shared transport lane {source_id}->{target_id} needs {total_load}kT, above any one "
                f"available hull ({largest_capacity}kT). Merge fleets {fleet_ids} at the source into "
                f"fleet {target_fleet_id} ({merged_capacity}kT), then load its combined colonist/mineral "
                "manifest and set the destination Transport unload-all-cargo task in this same X file."
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
    throughput = population_transport_throughput(state)
    reserve_population_capacity = bool(
        int(throughput["backlog_colonists"]) > 0
        and int(throughput["exportable_population"]) > 0
        and int(throughput["transport_shortfall"]) > 0
    )
    economy = decode_race_economy(state.race)
    modifier = float(plan.objective("logistics") * plan.mission("transport")) if plan else 1.0

    source_committed: dict[int, dict[str, int]] = {}
    target_inbound: dict[int, dict[str, int]] = {}
    population_inbound: dict[int, int] = {}
    population_source_outbound: dict[int, int] = {}
    dispatched_sources: set[int] = set()
    reserved_capture_target_ids={
        int(fleet.destination_planet_id)
        for fleet in state.fleets
        if fleet.owner == state.player_id
        and fleet.role == "freighter"
        and fleet.destination_planet_id is not None
        and any(
            int(planet.id) == int(fleet.destination_planet_id)
            and planet.owner is not None
            and int(planet.owner) != int(state.player_id)
            for planet in state.planets
        )
    }
    reserved_capture_target_ids.update(
        int(order.payload["destination_planet_id"])
        for order in orders.orders
        if order.kind == "transport_population"
        and bool((order.payload or {}).get("population_capture"))
        and (order.payload or {}).get("destination_planet_id") is not None
    )
    available: list[tuple[Any, Any]] = []
    schedule: list[dict[str, Any]] = []
    blocked_delivery_fleet_ids: list[int] = []
    scheduled_fleet_ids: set[int] = set()

    for fleet in state.fleets:
        if fleet.owner != state.player_id or fleet.role != "freighter" or fleet.destination_planet_id is not None:
            continue
        here = _planet_under_fleet(fleet, owned)
        if here is None:
            continue
        cargo = (fleet.native or {}).get("cargo", {}) or {}
        if _cargo_mass(fleet) > 0:
            # A transport which is already orbiting an owned planet with
            # cargo aboard has completed the flight but not the delivery.
            # Reapply the captured current-waypoint Transport task so Stars!
            # unloads every cargo class, including fields that happen to be
            # empty.  This deliberately recovers the individual hull without
            # stopping unrelated empty freighters from serving new lanes.
            payload={
                "fleet_id": int(fleet.id),
                "destination_planet_id": int(here.id),
                "warp": 0,
                "cargo_before": {key: int(cargo.get(key, 0) or 0) for key in (*_MINERALS, "population")},
                "unload": {key: "all" for key in (*_MINERALS, "population")},
                "mission": "transport",
                "native_reference": "captured current-orbit Type-5 Transport task with all I/B/G/population unload fields",
            }
            orders.add(
                "transport_unload_remainder",payload,
                f"Recover completed freight delivery: {fleet.name} is orbiting {here.name} with cargo aboard; "
                "set the current waypoint to unload all Ironium, Boranium, Germanium, and colonists.",
                priority=176,
            )
            scheduled_fleet_ids.add(int(fleet.id))
            schedule.append({
                "fleet_id": int(fleet.id),
                "kind": "unload_remainder",
                "status": "current_orbit_unload_all",
                "source_planet_id": int(here.id),
                "destination_planet_id": int(here.id),
                "cargo_before": dict(payload["cargo_before"]),
            })
            continue
        if _fleet_capacity(fleet) > 0:
            available.append((fleet, here))

    # Select one fleet at a time. Every selected manifest changes mineral and
    # population availability before the next selection, which is the required
    # reprioritization step rather than two independent transport passes.
    while available:
        candidates: list[tuple[float, int, int, int, dict[str, Any], Any]] = []
        for index, (fleet, source) in enumerate(available):
            for option in _population_capture_options(
                state, fleet, source, plan=plan, network=network, hubs=hubs,
                dispatched_sources=dispatched_sources,
                reserved_target_ids=reserved_capture_target_ids,
            ):
                candidates.append((float(option["score"]), 2, _fleet_capacity(fleet), -int(fleet.id), option, fleet))
            for option in _population_options(
                state, fleet, source, network=network, hubs=hubs, statuses=statuses,
                base_demands=base_demands, population_inbound=population_inbound,
                population_source_outbound=population_source_outbound,
            ):
                if reserve_population_capacity:
                    # A throughput deficit is an empire-level commitment.
                    # Reserve suitable empty holds from ordinary mineral work
                    # until the active shuttle lane has been filled.
                    option = dict(option)
                    option["score"] = float(option["score"]) + 2_500.0
                    option["population_transport_reservation"] = True
                candidates.append((float(option["score"]), 1, _fleet_capacity(fleet), -int(fleet.id), option, fleet))
            for option in _mineral_options(
                state, fleet, source, owned=owned, hubs=hubs, economy=economy,
                orders=orders, base_demands=base_demands,
                source_committed=source_committed, target_inbound=target_inbound,
            ):
                candidates.append((float(option["score"]), 0, _fleet_capacity(fleet), -int(fleet.id), option, fleet))
        if not candidates:
            break
        # On an exact need score, population receives the spare cargo rule;
        # otherwise destination need score is the sole primary decision.
        # Need score remains primary.  A capacity tie is won by the larger
        # freighter so it can satisfy more of the selected world's real need.
        _, _, _, _, selected, fleet = max(candidates, key=lambda row: row[:4])
        source = selected["source"]
        target = selected["target"]
        available = [(f, p) for f, p in available if int(f.id) != int(fleet.id)]
        score = float(selected["score"])

        if selected["kind"] == "population_capture":
            reserved_capture_target_ids.add(int(target.id))
            payload = _population_capture_payload(state, fleet, selected)
            orders.add(
                "transport_population",
                payload,
                (
                    f"{'Counter-capture' if bool(selected.get('recapture')) else 'Population-capture'} transport: {fleet.name} carries "
                    f"{selected['population_colonists']:,} colonists to overwhelm "
                    f"the observed {selected['target_info'].estimated_population:,}-colonist "
                    f"foreign colony at {target.name}; known Alternate Reality targets are excluded."
                ),
                priority=int(_order_priority(score) * modifier),
            )
            dispatched_sources.add(int(source.id))
            scheduled_fleet_ids.add(int(fleet.id))
            schedule.append({
                "fleet_id": int(fleet.id), "kind": "population_capture", "need_rank": round(score, 3),
                "source_planet_id": int(source.id), "destination_planet_id": int(target.id),
                "population_kt": int(selected["population_kt"]), "mineral_load": _empty_load(),
                "manifest_total_kt": int(selected["population_kt"]), "capacity_kt": _fleet_capacity(fleet),
            })
            continue

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
            population_source_outbound[int(source.id)] = (
                population_source_outbound.get(int(source.id), 0)
                + int(selected["population_colonists"])
            )
            dispatched_sources.add(int(source.id))
            scheduled_fleet_ids.add(int(fleet.id))
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
            "fuel": "unchanged",
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
        scheduled_fleet_ids.add(int(fleet.id))
        schedule.append({
            "fleet_id": int(fleet.id), "kind": "minerals", "need_rank": round(score, 3),
            "source_planet_id": int(source.id), "destination_planet_id": int(target.id),
            "mineral_load": dict(load), "manifest_total_kt": int(cargo_plan.total),
            "capacity_kt": int(cargo_plan.capacity),
        })

    # An idle transport at a non-exporting planet is capacity, not a reason to
    # queue another hull.  Re-stage it at the nearest ready population source
    # with real downstream work.  Freighters already selected for a delivery
    # above retain that higher-priority operation.
    for ret in plan_empty_freighter_returns(state):
        if int(ret.fleet_id) in scheduled_fleet_ids:
            continue
        orders.add(
            "move_fleet",
            ret.to_payload(),
            ret.reason,
            priority=132,
        )
        scheduled_fleet_ids.add(int(ret.fleet_id))
        schedule.append({
            "fleet_id": int(ret.fleet_id),
            "kind": "return_for_population_export",
            "source_planet_id": int(ret.current_planet_id),
            "destination_planet_id": int(ret.export_planet_id),
            "downstream_population_backlog": int(ret.downstream_population_backlog),
        })

    if blocked_delivery_fleet_ids:
        orders.notes.append(
            "LOGISTICS DELIVERY QUARANTINE: native destination waypoints were consumed with cargo still aboard "
            f"freighter(s) {blocked_delivery_fleet_ids}; those hulls are held while empty freighters continue "
            "using the client-controlled four-cargo unload endpoint."
        )

    _consolidate_bulk_lanes(state, orders, schedule)
    scheduled_by_fleet={int(row["fleet_id"]):row for row in schedule}
    population_transport=[]
    for fleet in state.fleets:
        if fleet.owner != state.player_id or fleet.role != "freighter":
            continue
        row=scheduled_by_fleet.get(int(fleet.id))
        native=fleet.native or {}
        cargo=native.get("cargo",{}) or {}
        population_kt=int(cargo.get("population",0) or 0)
        capacity=_fleet_capacity(fleet)
        if row is not None:
            kind=str(row.get("kind", ""))
            status=(
                "loading" if kind in {"population","population_capture"}
                else "returning" if kind=="return_for_population_export"
                else "unloading" if kind=="unload_remainder"
                else "blocked"
            )
            blocked_reason=None if status!="blocked" else "assigned to non-population material duty"
        elif population_kt>0:
            status="unloading" if _planet_under_fleet(fleet,owned) is not None else "outbound"
            blocked_reason=None
        elif fleet.destination_planet_id is not None:
            status="returning"
            blocked_reason=None
        else:
            status="blocked"
            blocked_reason="no ready exporter or recipient after shared scheduling"
        population_transport.append({
            "fleet_id":int(fleet.id),
            "state":status,
            "exporter_id":(
                None if row is None else
                row.get("destination_planet_id")
                if str(row.get("kind", ""))=="return_for_population_export"
                else row.get("source_planet_id")
            ),
            "recipient_id":(
                None if row is None or str(row.get("kind", ""))=="return_for_population_export"
                else row.get("destination_planet_id")
            ),
            "capacity":capacity,
            "last_delivery":native.get("last_population_delivery_year"),
            "blocked_reason":blocked_reason,
        })
    state.native["shared_transport_schedule"] = {
        "policy": "rank destination need globally; recompute source surplus and destination need after each fleet; dispatch every useful available transport while preserving each source floor",
        "assignments": schedule,
        "native_delivery_recovery_blocked_fleet_ids": blocked_delivery_fleet_ids,
        "unassigned_freighter_ids": sorted(int(f.id) for f, _ in available),
        "population_inbound_colonists": {str(k): int(v) for k, v in population_inbound.items()},
        "mineral_inbound_kt": {str(k): dict(v) for k, v in target_inbound.items()},
        "reserved_capture_target_ids": sorted(reserved_capture_target_ids),
    }
    state.native["population_transport"]=population_transport
    population_fleet_ids={
        int(row["fleet_id"])
        for row in schedule
        if str(row.get("kind", "")) in {
            "population", "population_capture", "return_for_population_export",
        }
    }
    state.native["population_transport_throughput"] = population_transport_throughput(
        state,
        evaluate_logistics_capacity(state),
        assigned_population_fleet_ids=population_fleet_ids,
    ) | {
        "reserved_from_mineral_work": bool(reserve_population_capacity),
        "scheduled_population_fleet_ids": sorted(population_fleet_ids),
    }
    return schedule
