"""Round-trip population logistics for the onion expansion strategy.

Opening doctrine:

* the homeworld moves at most 8,000 colonists / 80 kT per turn until it reaches
  200,000 colonists, then at most 20,000 colonists / 200 kT;
* a source normally dispatches at most ONE population transport per turn;
* the opening homeworld starts pulsing at about 100,000 population and keeps
  about 80,000 behind after a departure;
* a graduated child hub keeps at least ~25% of racial planet capacity before
  exporting onward;
* the homeworld feeds only designated, not-yet-graduated Layer-1 hubs;
* after a Layer-1 hub has a shipyard/refuel starbase AND ~25% population, the
  homeworld stops feeding it and the hub becomes an exporter to Layer 2;
* empty transports return only to an immediately loadable export hub so scarce
  early Privateer/Medium-Freighter-class hulls do not waste a flight waiting.

Loaded population never receives stargate range credit. The freight leg flies
normally; only an empty ship may later use a gate once native gate movement is
validated.

The Type-2 source-load form is host-accepted at 200 kT. Other quantities and a
combined Type-2 population plus Type-1 mineral load are enabled experiments.
Every experiment is carried in the native decision trace with its exact block
sequence, so a host rejection can be isolated without disabling the capability.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from types import SimpleNamespace
from typing import Any

from .cargo_planner import derive_cargo_plan
from .expansion_network import evaluate_expansion_network
from .fuel_planner import (
    fastest_fuel_safe_warp,
    mission_reachable_with_planned_cargo,
    profile_with_planned_cargo,
)
from .logistics_capacity import (
    HOMEWORLD_EARLY_EXPORT_KT,
    POPULATION_PULSE_COLONISTS,
    POPULATION_PULSE_KT,
    evaluate_logistics_capacity,
    export_source_statuses,
)
from .models import OrderSet
from .planet_economy import decode_race_economy
from .util import distance
from .warp_policy import mission_warp

COLONISTS_PER_KT = 100
MIN_POPULATION_CARRIER_KT = HOMEWORLD_EARLY_EXPORT_KT


@dataclass(frozen=True)
class PopulationTransferIntent:
    fleet_id: int
    source_planet_id: int
    destination_planet_id: int
    population_kt: int
    population_colonists: int
    warp: int
    score: float
    source_ring: int
    destination_ring: int
    source_population_before: int
    source_population_after: int
    source_protected_floor: int
    mineral_load: dict[str, int]
    native_experiment: dict[str, Any]
    reason: str

    def to_payload(self) -> dict:
        return asdict(self) | {
            "mission": "transport",
            "unload": {
                "ironium": "all",
                "boranium": "all",
                "germanium": "all",
                "population": "all",
            },
            "fuel": "load_optimal",
            "native_population_encoding": "type2_97_00_12_08_le16",
            "gate_allowed_while_loaded": False,
            "round_trip_logistics": True,
            "population_dispatch_policy": "one_capped_export_per_source_per_turn",
        }


@dataclass(frozen=True)
class EmptyFreighterReturnIntent:
    fleet_id: int
    current_planet_id: int
    export_planet_id: int
    warp: int
    export_ring: int
    downstream_population_backlog: int
    score: float
    reason: str

    def to_payload(self) -> dict:
        return {
            "fleet_id": self.fleet_id,
            "destination_planet_id": self.export_planet_id,
            "warp": self.warp,
            "mission": "return_for_population_export",
            "round_trip_logistics": True,
            "population_export_hub_id": self.export_planet_id,
            "downstream_population_backlog": self.downstream_population_backlog,
            "gate_preferred_if_legal": True,
            "gate_native_status": "flight_fallback_until_gate_order_validated",
        }


def _planet_under_fleet(fleet, owned):
    pid = int((fleet.native or {}).get("position_object_id", -1))
    direct = next((p for p in owned if int(p.id) == pid), None)
    if direct is not None:
        return direct
    return next((
        p for p in owned
        if abs(float(p.position.x) - float(fleet.position.x)) <= 0.5
        and abs(float(p.position.y) - float(fleet.position.y)) <= 0.5
    ), None)


def _cargo_mass(fleet) -> int:
    cargo = (fleet.native or {}).get("cargo", {}) or {}
    return sum(int(cargo.get(k, 0) or 0) for k in (
        "ironium", "boranium", "germanium", "population"
    ))


def _receiver_score(state, planet, hub) -> float:
    cap = max(1, int(getattr(hub, "capacity", 1) or 1))
    frac = int(planet.population or 0) / cap
    hab = int(planet.habitability if planet.habitability is not None else 0)
    strategic = float((planet.native or {}).get("strategic_value", 0.5) or 0.5)
    score = 0.0
    # A nearly-graduated hub remains valuable, but the planner never rounds a
    # packet up past its remaining need merely to make the arithmetic exact.
    score += 2.5 * min(1.0, max(0.0, (0.25 - frac) / 0.25))
    score += 0.012 * max(0, hab)
    score += 0.20 * min(6, int(getattr(hub, "frontier_worlds_160", 0) or 0))
    score += 0.34 * max(0, int(getattr(hub, "ring", 0) or 0))
    score += 0.40 * strategic
    if bool(getattr(hub, "designated_layer1", False)):
        score += 2.0
    return score


def _available_empty_population_freighters(state):
    """Small/medium population carriers; bulk freighters are not preferred here."""
    out = []
    for f in state.fleets:
        if f.owner != state.player_id or f.role != "freighter" or f.destination_planet_id is not None:
            continue
        if _cargo_mass(f) != 0:
            continue
        cap = int((f.native or {}).get("cargo_capacity", 0) or 0)
        if cap < MIN_POPULATION_CARRIER_KT:
            continue
        out.append(f)
    return out


def _safe_empty_warp(fleet, target) -> int | None:
    fp = (fleet.native or {}).get("fuel_profile")
    if fp:
        flags = (fleet.native or {}).get("race_fuel_flags", {}) or {}
        safe = fastest_fuel_safe_warp(
            fp,
            distance(fleet.position, target.position),
            "return_for_population_export",
            ife=bool(flags.get("ife")),
            ce=bool(flags.get("ce")),
        )
        return None if safe is None else int(safe)
    return int(mission_warp(fleet, target.position, "return_for_population_export"))


def plan_population_redistribution(
    state: Any,
    plan=None,
    *,
    orders=None,
    max_transfers: int = 4,
) -> list[PopulationTransferIntent]:
    """Plan phased parent->child population exports.

    The dispatch budget is centralized per source. Two transports sitting on the
    same homeworld cannot both independently decide to load population in the
    same turn. That phasing protects the breeder and lets it replenish between
    departures.
    """
    owned = [p for p in state.planets if p.owner == state.player_id]
    if len(owned) < 2:
        return []

    network = evaluate_expansion_network(state)
    hubs = {int(h.planet_id): h for h in network.hubs}
    planets = {int(p.id): p for p in owned}
    statuses = {x.planet_id: x for x in export_source_statuses(state, network)}

    # A child must still need one native-encodable 100-colonist (1 kT) packet.
    receivers = []
    for hub in network.hubs:
        parent_id = hub.parent_exporter_id
        need = int(hub.import_population_to_25 or 0)
        if parent_id is None or need < COLONISTS_PER_KT:
            continue
        p = planets.get(int(hub.planet_id))
        if p is None or (p.habitability is not None and int(p.habitability) <= 0):
            continue
        receivers.append((_receiver_score(state, p, hub), p, need, hub, int(parent_id)))
    receivers.sort(key=lambda row: (-row[0], row[3].ring, row[1].id))
    if not receivers:
        return []

    freighters = _available_empty_population_freighters(state)
    if not freighters:
        return []

    dispatched_sources: set[int] = set()
    used_fleets: set[int] = set()
    planned_to: dict[int, int] = {}
    intents: list[PopulationTransferIntent] = []

    for receiver_score, target, raw_need, target_hub, parent_id in receivers:
        if len(intents) >= int(max_transfers):
            break
        if parent_id in dispatched_sources:
            # Hard opening micro invariant: no 2+ population loads from one
            # export planet in the same turn.
            continue
        status = statuses.get(parent_id)
        source_hub = hubs.get(parent_id)
        source = planets.get(parent_id)
        if status is None or source_hub is None or source is None or not status.ready_now:
            continue
        remaining_need = int(raw_need) - int(planned_to.get(int(target.id), 0))
        export_colonists = min(
            int(status.max_export_colonists),
            max(0, int(status.exportable_population)),
            max(0, remaining_need),
        )
        # The native population quantity is kT, i.e. an exact multiple of 100
        # colonists. Do not round up a load beyond the source or target need.
        export_colonists = (export_colonists // COLONISTS_PER_KT) * COLONISTS_PER_KT
        export_kt = export_colonists // COLONISTS_PER_KT
        if export_kt <= 0:
            continue

        choices = []
        for fleet in freighters:
            if int(fleet.id) in used_fleets:
                continue
            here = _planet_under_fleet(fleet, owned)
            if here is None or int(here.id) != parent_id:
                continue
            cargo_cap = int((fleet.native or {}).get("cargo_capacity", 0) or 0)
            if cargo_cap < export_kt:
                continue
            if int(source.population or 0) - export_colonists < int(status.protected_floor):
                continue
            if not mission_reachable_with_planned_cargo(
                fleet, target.position, "transport", {"population": export_kt}
            ):
                continue
            travel = distance(source.position, target.position)
            # Prefer long-range compact population carriers over an oversized
            # bulk freighter when both are otherwise usable.
            oversize_penalty = max(0, cargo_cap - 300) / 600.0
            choices.append((receiver_score - travel / 500.0 - oversize_penalty, fleet, cargo_cap))

        if not choices:
            continue
        score, fleet, cargo_cap = max(choices, key=lambda row: row[0])

        # Use the otherwise-unused cargo hold for the recipient's real mineral
        # deficit. The shared reserve policy prevents stripping the source.
        # This combined population+mineral encoding is experimental, so it is
        # fully logged by the native writer.
        mineral_load = {"ironium": 0, "boranium": 0, "germanium": 0}
        residual_capacity = max(0, int(cargo_cap) - int(export_kt))
        planning_orders = orders or OrderSet(state.game_name, state.year, state.player_id)
        if residual_capacity:
            proxy_fleet = SimpleNamespace(cargo_capacity=residual_capacity, native={"cargo_capacity": residual_capacity})
            cargo_plan = derive_cargo_plan(
                source, target, proxy_fleet, decode_race_economy(state.race), planning_orders,
            )
            if cargo_plan is not None:
                mineral_load = cargo_plan.as_load()

        planned_cargo = {"population": export_kt, **mineral_load}
        # A mineral top-off must not invalidate the fuel-safe population route.
        if sum(mineral_load.values()) and not mission_reachable_with_planned_cargo(
            fleet, target.position, "transport", planned_cargo
        ):
            mineral_load = {"ironium": 0, "boranium": 0, "germanium": 0}
            planned_cargo = {"population": export_kt}

        fp = (fleet.native or {}).get("fuel_profile")
        if fp:
            flags = (fleet.native or {}).get("race_fuel_flags", {}) or {}
            planned = profile_with_planned_cargo(fp, planned_cargo)
            safe_warp = fastest_fuel_safe_warp(
                planned,
                distance(fleet.position, target.position),
                "transport",
                ife=bool(flags.get("ife")),
                ce=bool(flags.get("ce")),
            )
            if safe_warp is None:
                continue
            selected_warp = int(safe_warp)
        else:
            selected_warp = int(mission_warp(fleet, target.position, "transport"))

        before = int(source.population or 0)
        after = before - export_colonists
        dispatched_sources.add(parent_id)
        used_fleets.add(int(fleet.id))
        planned_to[int(target.id)] = int(planned_to.get(int(target.id), 0)) + export_colonists

        mixed_load = sum(mineral_load.values()) > 0
        baseline_200kt = export_kt == POPULATION_PULSE_KT and not mixed_load
        experiment_id = (
            "population-type2-200kt-baseline"
            if baseline_200kt
            else f"population-type2-{export_kt}kt" + ("-with-minerals" if mixed_load else "")
        )
        native_experiment = {
            "enabled": True,
            "id": experiment_id,
            "trust_level": "VALIDATED" if baseline_200kt else "EXPERIMENTAL",
            "validated_baseline": "200 kT Type-2 97 00 12 08 + LE16 quantity with 0x11 population transport task",
            "risk": (
                "None beyond the controlled population transport baseline."
                if baseline_200kt
                else "Type-2 quantity and/or combined Type-1 mineral source-load has not yet been client-captured."
            ),
            "mineral_capacity_kt": int(residual_capacity),
        }

        intents.append(PopulationTransferIntent(
            fleet_id=int(fleet.id),
            source_planet_id=parent_id,
            destination_planet_id=int(target.id),
            population_kt=export_kt,
            population_colonists=export_colonists,
            warp=selected_warp,
            score=round(float(score), 3),
            source_ring=int(source_hub.ring),
            destination_ring=int(target_hub.ring),
            source_population_before=before,
            source_population_after=after,
            source_protected_floor=int(status.protected_floor),
            mineral_load=mineral_load,
            native_experiment=native_experiment,
            reason=(
                f"Onion population pulse {source.name} (ring {source_hub.ring})->{target.name} "
                f"(ring {target_hub.ring}): load {export_colonists:,} colonists "
                f"({export_kt} kT) into {fleet.name} ({cargo_cap} kT hold)"
                f"; minerals I/B/G={mineral_load['ironium']}/{mineral_load['boranium']}/{mineral_load['germanium']} kT. "
                f"Projected source {before:,}->{after:,}; protected floor={status.protected_floor:,}. "
                "This is the only population departure allowed from this source this turn. Loaded cargo "
                "flies normally; after destination unload the empty hull returns/repositions for another pulse."
            ),
        ))
    return intents


def plan_empty_freighter_returns(
    state: Any,
    transfer_intents: list[PopulationTransferIntent] | tuple[PopulationTransferIntent, ...] = (),
    *,
    max_returns: int = 5,
) -> list[EmptyFreighterReturnIntent]:
    """Route idle empty transports back to useful export hubs.

    An empty leg is only useful when the destination can load cargo immediately.
    A transport may therefore return to a population exporter only when that
    world has both an active downstream need and a currently legal population
    packet.  This avoids consuming early fuel and travel time to park a scarce
    freighter at a breeder that has nothing ready to move.
    """
    owned = [p for p in state.planets if p.owner == state.player_id]
    if not owned:
        return []
    network = evaluate_expansion_network(state)
    logistics = evaluate_logistics_capacity(state)
    hubs = {int(h.planet_id): h for h in network.hubs}
    planets = {int(p.id): p for p in owned}
    statuses = {x.planet_id: x for x in logistics.exporter_status}
    used = {int(x.fleet_id) for x in transfer_intents}
    dispatched_sources = {int(x.source_planet_id) for x in transfer_intents}

    exporters = []
    for pid, status in statuses.items():
        hub = hubs.get(pid)
        p = planets.get(pid)
        if (
            hub is None
            or p is None
            or int(status.downstream_backlog) < int(status.max_export_colonists)
            or not bool(status.ready_now)
            or int(status.exportable_population) < COLONISTS_PER_KT
            # A source has one legal population departure each turn.  Once a
            # sibling freighter has claimed it, do not send another hull there
            # empty on the strength of its pre-dispatch population snapshot.
            or int(pid) in dispatched_sources
        ):
            continue
        exporters.append((pid, p, hub, status))
    if not exporters:
        return []

    # Count empty hulls already staged at each exporter so return traffic spreads
    # across active sources rather than creating a parking lot at the homeworld.
    staged = {pid: 0 for pid, *_ in exporters}
    empty_freighters = _available_empty_population_freighters(state)
    for fleet in empty_freighters:
        here = _planet_under_fleet(fleet, owned)
        if here is not None and int(here.id) in staged:
            staged[int(here.id)] += 1

    returns: list[EmptyFreighterReturnIntent] = []
    for fleet in empty_freighters:
        if len(returns) >= int(max_returns) or int(fleet.id) in used:
            continue
        current = _planet_under_fleet(fleet, owned)
        if current is None:
            continue
        if int(current.id) in staged:
            # Already waiting at a designated exporter. Phasing means it should
            # stay idle if another hull got this turn's one allowed pulse.
            continue

        choices = []
        for pid, export_planet, hub, status in exporters:
            warp = _safe_empty_warp(fleet, export_planet)
            if warp is None:
                continue
            travel = distance(current.position, export_planet.position)
            congestion = int(staged.get(pid, 0))
            # A source that can sustain only ~0.2 pulse/turn does not need three
            # waiting freighters. Sustainable rate and backlog both matter.
            score = (
                2.2 * min(1.0, float(status.sustainable_pulses_per_turn) + (0.35 if status.ready_now else 0.0))
                + min(2.5, int(status.downstream_backlog) / 80_000.0)
                + 0.35 * int(hub.ring)
                - travel / 300.0
                - 1.05 * congestion
            )
            choices.append((score, pid, export_planet, hub, status, warp))
        if not choices:
            continue
        score, pid, export_planet, hub, status, warp = max(choices, key=lambda row: row[0])
        staged[pid] = staged.get(pid, 0) + 1
        returns.append(EmptyFreighterReturnIntent(
            fleet_id=int(fleet.id),
            current_planet_id=int(current.id),
            export_planet_id=int(pid),
            warp=int(warp),
            export_ring=int(hub.ring),
            downstream_population_backlog=int(status.downstream_backlog),
            score=round(float(score), 3),
            reason=(
                f"Round-trip onion logistics: {fleet.name} is empty at {current.name}; reposition to "
                f"ring-{hub.ring} exporter {export_planet.name}. Source currently has {status.population:,} "
                f"colonists, with an immediate {status.exportable_population:,}-colonist exportable payload "
                f"above trigger={status.dispatch_trigger:,} and protected floor={status.protected_floor:,}, "
                f"sustainable rate~{status.sustainable_pulses_per_turn:.2f} "
                f"{status.max_export_colonists:,}-colonist exports/turn, downstream "
                f"backlog={status.downstream_backlog:,}. Wait there if another hull already used this turn's "
                "single source dispatch."
            ),
        ))
    return returns


def add_population_redistribution_orders(state: Any, orders, plan=None) -> list[PopulationTransferIntent]:
    transfers = plan_population_redistribution(state, plan, orders=orders)
    for intent in transfers:
        orders.add(
            "transport_population",
            intent.to_payload(),
            intent.reason,
            priority=138 if int(state.year) <= 2430 else 124,
        )

    for ret in plan_empty_freighter_returns(state, transfers):
        orders.add(
            "move_fleet",
            ret.to_payload(),
            ret.reason,
            priority=129 if int(state.year) <= 2430 else 115,
        )
    return transfers
