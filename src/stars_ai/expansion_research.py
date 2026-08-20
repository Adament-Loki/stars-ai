"""Capability-driven research demands for early onion-ring expansion.

This layer describes *why* technology is valuable.  The shared research
planner remains responsible for scoring, hysteresis, sprint selection, and
native ResearchChange emission.

Hard race rule: Space Dock and Ultra Station are available only to races with
the Improved Starbases (ISB) LRT.  Construction 4 must never be interpreted as
a universal Space Dock unlock.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .expansion_network import ExpansionNetworkSnapshot, evaluate_expansion_network
from .logistics_capacity import evaluate_logistics_capacity


PRT_HE = 0
PRT_IS = 4
PRT_IT = 7


@dataclass(frozen=True)
class ExpansionResearchDemand:
    capability_id: str
    name: str
    category: str
    requirements: dict[str, int]
    post_unlock_action: str
    need: float
    urgency: float
    value: float
    utilization: float
    explanation: str
    executable: bool = True
    tags: tuple[str, ...] = ("expansion_enabler", "onion_network")

    def to_external_demand(self) -> dict:
        return asdict(self)


def _prt_id(state: Any) -> int | None:
    native = getattr(state.race, "native", {}) or {}
    value = native.get("prt_id")
    if value is not None:
        return int(value)
    name = str(getattr(state.race, "primary_trait", "") or "").casefold()
    if "hyper expansion" in name:
        return PRT_HE
    if "inner strength" in name:
        return PRT_IS
    if "interstellar traveler" in name or "interstellar traveller" in name:
        return PRT_IT
    return None


def _lrts(state: Any) -> set[str]:
    return {
        str(x).upper()
        for x in ((getattr(state.race, "native", {}) or {}).get("lrts", []) or [])
    }


def _remaining(state: Any, requirements: dict[str, int]) -> dict[str, int]:
    return {
        field: max(0, int(level) - int(getattr(state.tech, field, 0) or 0))
        for field, level in requirements.items()
        if int(level) > int(getattr(state.tech, field, 0) or 0)
    }


def _locked(state: Any, requirements: dict[str, int]) -> bool:
    return bool(_remaining(state, requirements))


def _best_existing_cargo(state: Any) -> int:
    return max(
        (
            int(d.get("cargo_capacity", 0) or 0)
            for d in ((state.native or {}).get("design_profiles", []) or [])
            if not d.get("is_starbase", False) and d.get("role") == "freighter"
        ),
        default=0,
    )


def expansion_research_demands(
    state: Any,
    network: ExpansionNetworkSnapshot | None = None,
) -> list[dict]:
    """Return current expansion-capability demands for the shared planner.

    Opening doctrine through about turn 30:
      * expand/consolidate in ~160 ly parent/child rings toward 300-500 ly reach;
      * move population/minerals outward so child hubs become new breeders and
        shipbuilding/refuel launch points;
      * prefer exact enabling capabilities over generic field balancing;
      * delay unrelated mature technology unless a military emergency overrides.
    """
    network = network or evaluate_expansion_network(state)
    turn = int(network.turn)
    prt = _prt_id(state)
    lrts = _lrts(state)
    has_isb = "ISB" in lrts
    best_cargo = _best_existing_cargo(state)
    logistics = evaluate_logistics_capacity(state)
    out: list[ExpansionResearchDemand] = []

    opening_pressure = bool(turn <= 30 and network.expansion_network_debt)
    persistent_pressure = bool(turn > 30 and network.expansion_network_debt)
    if not opening_pressure and not persistent_pressure:
        return []

    phase_boost = 1.35 if opening_pressure else 1.0
    radius_pressure = min(3.0, 1.0 + network.radius_gap_ly / 140.0)
    logistics_pressure = min(
        3.0,
        1.0
        + network.population_import_backlog / 180_000.0
        + network.bootstrap_germanium_deficit / 180.0,
    )

    # IFE's Fuel Mizer is a named early range unlock; do not research
    # Propulsion merely because the field is low.
    if "IFE" in lrts and _locked(state, {"propulsion": 2}) and network.needs_range_infrastructure:
        out.append(ExpansionResearchDemand(
            "component:fuel_mizer",
            "Fuel Mizer engine",
            "expansion",
            {"propulsion": 2},
            "Create/upgrade scouts, colony ships, and logistics hulls with Fuel Mizer where mission scoring prefers it.",
            need=max(1.6, radius_pressure) * phase_boost,
            urgency=1.8 * phase_boost,
            value=2.4,
            utilization=1.0,
            explanation=(
                f"Opening network radius is {network.owned_radius_ly:.0f}/{network.target_radius_ly:.0f} ly; "
                f"{network.frontier_worlds_beyond_ungated_hop} target-world(s) lie beyond a mature parent's "
                "normal ~160 ly expansion hop. IFE makes Fuel Mizer a named direct range unlock."
            ),
        ))

    # Construction 4 is a compound *race-aware* opening breakpoint.
    # Everyone can value the Privateer. ISB adds Space Dock. Inner Strength
    # adds Fuel Transport. Space Dock must never appear without ISB.
    c4_req = {"construction": 4}
    c4_base_pressure = bool(has_isb and (network.hubs_missing_shipyard or network.hubs_missing_refuel))
    if _locked(state, c4_req) and (
        network.needs_range_infrastructure
        or logistics_pressure > 1.2
        or c4_base_pressure
    ):
        package = ["Privateer"]
        if has_isb:
            package.append("Space Dock")
        if prt == PRT_IS:
            package.append("Fuel Transport")
        package_text = " + ".join(package)

        actions = ["develop the onion Privateer: one quality engine plus three basic Fuel Tanks for repeated 20k-population/hub-bootstrap runs"]
        if has_isb:
            actions.append("establish cheap Space Dock shipyard/refuel hubs on the next ring")
        if prt == PRT_IS:
            actions.append("use Fuel Transport as a dedicated fuel-export asset")

        out.append(ExpansionResearchDemand(
            "expansion:frontier_logistics_c4",
            f"Frontier Logistics C4 ({package_text})",
            "expansion",
            c4_req,
            "; ".join(actions) + ".",
            need=max(radius_pressure, logistics_pressure) * phase_boost,
            urgency=2.0 * phase_boost,
            value=2.8,
            utilization=1.0,
            explanation=(
                f"Race package={package_text}; ISB={'yes' if has_isb else 'no'}; "
                f"outer hubs missing shipyard/refuel={network.hubs_missing_shipyard}/{network.hubs_missing_refuel}; "
                f"population import backlog={network.population_import_backlog:,}; desired compact population freighters={logistics.desired_population_freighters}; "
                f"race-legal bootstrap base={network.bootstrap_base_name}; "
                f"bootstrap I/B/G deficit={network.bootstrap_ironium_deficit}/"
                f"{network.bootstrap_boranium_deficit}/{network.bootstrap_germanium_deficit}."
            ),
        ))

    # Inner Strength gets a second dedicated tanker hull at C7. Other races can
    # use normal fuel tanks/transport hulls rather than being forced into an
    # unrelated Propulsion ladder.
    if (
        prt == PRT_IS
        and _locked(state, {"construction": 7})
        and network.deepest_owned_ring >= 2
        and network.needs_range_infrastructure
    ):
        out.append(ExpansionResearchDemand(
            "hull:super_fuel_xport",
            "Super-Fuel Xport",
            "expansion",
            {"construction": 7},
            "Create dedicated long-range fuel-export fleets supporting outer-ring colonizers/freighters.",
            need=1.5 * phase_boost,
            urgency=1.3 * phase_boost,
            value=2.0,
            utilization=0.8,
            explanation="Inner Strength has reached a multi-ring empire where dedicated fuel relay capacity has strategic value.",
        ))

    # Large Freighter is an INDUSTRIAL bulk-logistics unlock, not the answer to
    # opening population movement. Population uses aggressively cycled 200-kT
    # Privateer/Medium-Freighter-class pulses. C8 matters when large mineral
    # stockpiles need to be concentrated at fleet-construction shipyards.
    if (
        _locked(state, {"construction": 8})
        and logistics.large_freighter_valuable
        and best_cargo < 1200
    ):
        bulk_pressure=min(3.0,1.4 + logistics.bulk_transferable_kt / 900.0)
        out.append(ExpansionResearchDemand(
            "hull:2",
            "Large Freighter",
            "industrial_logistics",
            {"construction": 8},
            "Create a Large Freighter for bulk I/B/G concentration at active fleet-construction shipyards after native design verification.",
            need=bulk_pressure * (1.0 if turn <= 30 else 1.15),
            urgency=min(2.4,1.15 + 0.35*logistics.active_shipyard_build_count),
            value=min(3.2,1.8 + logistics.bulk_transferable_kt / 1200.0),
            utilization=1.0,
            explanation=(
                f"Best current freighter cargo={best_cargo} kT; bulk shipyard mineral deficit="
                f"{logistics.bulk_shipyard_deficit_kt} kT; donor surplus={logistics.bulk_donor_surplus_kt} kT; "
                f"transferable bulk={logistics.bulk_transferable_kt} kT; active shipyard builds="
                f"{logistics.active_shipyard_build_count}. Population backlog alone does not trigger C8."
            ),
            executable=False,
        ))

    # HE has no stargates. Other races can use the normal P5/C5 100/250 gate on
    # a legal starbase. ISB is NOT required for the gate itself; without ISB the
    # planner must use a normal legal starbase (e.g. Space Station), never a
    # fictional Space Dock.
    if prt != PRT_HE and network.needs_gate_network and _locked(
        state, {"propulsion": 5, "construction": 5}
    ):
        out.append(ExpansionResearchDemand(
            "gate:100_250",
            "Stargate 100/250",
            "expansion",
            {"propulsion": 5, "construction": 5},
            (
                f"Install 100/250 gates on selected parent/child {network.bootstrap_base_name} or other legal starbase hubs; "
                "unload population/minerals before gating and use the gate only to return or reposition EMPTY ships."
            ),
            need=max(1.4, radius_pressure) * phase_boost,
            urgency=1.65 * phase_boost,
            value=min(3.0, 1.8 + 0.25 * network.gate_pair_opportunities),
            utilization=min(1.0, 0.55 + 0.15 * network.gate_pair_opportunities),
            explanation=(
                f"{network.gate_pair_opportunities} mature hub pair(s) are 80-250 ly apart; "
                f"ISB={'yes' if has_isb else 'no'}, so the race-legal base path is {network.bootstrap_base_name}. "
                "Stars! unloads cargo before gating: loaded population/mineral freight still flies each ring leg normally. "
                "Gate value here is faster empty-freighter return/reposition plus empty military/scout redeployment."
            ),
            executable=False,
        ))

    # IT-specific unlimited-mass/300 gate: heavy-logistics upgrade, not a
    # universal opening target.
    if (
        prt == PRT_IT
        and network.needs_gate_network
        and logistics.large_freighter_valuable
        and _locked(state, {"propulsion": 6, "construction": 10})
    ):
        out.append(ExpansionResearchDemand(
            "gate:any_300",
            "Stargate any/300",
            "expansion",
            {"propulsion": 6, "construction": 10},
            "Upgrade key IT logistics hubs so EMPTY heavy freighters can return/reposition through the developed core/outer ring after unloading cargo.",
            need=1.35 * phase_boost,
            urgency=1.15 * phase_boost,
            value=2.4,
            utilization=0.8,
            explanation=(
                f"IT heavy-logistics gate is valuable because the network has gate opportunities and {logistics.bulk_transferable_kt} kT of transferable bulk industrial minerals. "
                "Cargo is never credited as gated throughput: unload first, then gate the empty hull."
            ),
            executable=False,
        ))

    out.sort(
        key=lambda d: (d.need * d.urgency * d.value * d.utilization),
        reverse=True,
    )
    return [d.to_external_demand() for d in out]
