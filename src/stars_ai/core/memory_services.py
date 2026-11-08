"""Focused algorithms over persistent AI memory.

``AgentMemory`` remains the public compatibility object and on-disk schema.
These services give the durable behaviors explicit owners without changing the
data model, planner call order, or native-writer boundary.  The legacy class
forwards its public methods here; its private implementation helpers remain an
intentional incremental-migration seam until a later phase can move them
without changing their well-tested behavior.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .memory import AgentMemory


class IntelligenceMemoryService:
    """Reconcile current Stars! observations with durable planet intelligence."""

    def reconcile(self, memory: "AgentMemory", state) -> dict[str, Any]:
        return memory._reconcile_state(state)


class ActionOutcomeService:
    """Maintain emitted-command expectations and later observable outcomes."""

    def record_emitted(self, memory: "AgentMemory", emitted, state) -> None:
        memory._record_emitted_actions(emitted, state)

    def evaluate(self, memory: "AgentMemory", state) -> list[dict[str, Any]]:
        return memory._evaluate_action_outcomes(state)


class ExplorationMemoryService:
    """Maintain scout route and scan-target continuity between turns."""

    def recent_scan_target_ids(
        self, memory: "AgentMemory", year: int, cooldown_years: int = 3,
    ) -> set[int]:
        return memory._recent_scan_target_ids(year, cooldown_years)

    def record_scan_orders(self, memory: "AgentMemory", orders, year: int) -> None:
        memory._record_scan_orders(orders, year)

    def record_emitted_scan_orders(self, memory: "AgentMemory", emitted, year: int) -> None:
        memory._record_emitted_scan_orders(emitted, year)

    def scout_route(self, memory: "AgentMemory", fleet_id: int) -> dict[str, Any] | None:
        return memory._scout_route(fleet_id)

    def set_scout_route(
        self,
        memory: "AgentMemory",
        fleet_id: int,
        planet_ids: list[int],
        year: int,
        *,
        expected_discoveries: int | None = None,
        total_distance: float | None = None,
        sector_index: int | None = None,
        sector_count: int | None = None,
        terminal: bool = True,
        awaiting_refuel: bool = False,
        waypoints: list[dict[str, int]] | None = None,
    ) -> None:
        memory._set_scout_route(
            fleet_id,
            planet_ids,
            year,
            expected_discoveries=expected_discoveries,
            total_distance=total_distance,
            sector_index=sector_index,
            sector_count=sector_count,
            terminal=terminal,
            awaiting_refuel=awaiting_refuel,
            waypoints=waypoints,
        )

    def clear_scout_route(self, memory: "AgentMemory", fleet_id: int) -> None:
        memory._clear_scout_route(fleet_id)

    def sync_from_native(self, memory: "AgentMemory", state) -> None:
        memory._sync_scout_routes_from_native(state)

    def prune(self, memory: "AgentMemory", state) -> None:
        memory._prune_scout_routes(state)

    def reserved_targets(
        self, memory: "AgentMemory", exclude_fleet_id: int | None = None,
    ) -> set[int]:
        return memory._reserved_scout_route_targets(exclude_fleet_id)


class KPIHistoryService:
    """Read and retain the durable strategic KPI history."""

    def discoveries_in_recent_years(
        self, memory: "AgentMemory", year: int, window: int = 5,
    ) -> int:
        floor = int(year) - max(0, int(window) - 1)
        return sum(
            1 for item in memory.planet_intel.values()
            if item.get("first_seen_year") is not None
            and int(item["first_seen_year"]) >= floor
        )

    def new_colonies_count(self, memory: "AgentMemory") -> int:
        # Retain the full history, but count only worlds still remembered as
        # ours for the live expansion posture.
        return sum(
            1 for key in memory.colonized_years
            if int((memory.planet_intel.get(str(key), {}) or {}).get(
                "last_known_owner", -1
            ) or -1) == int(memory.player_id or -1)
        )

    def scan_order_count(self, memory: "AgentMemory") -> int:
        return sum(
            int(item.get("assignment_count", 0))
            for item in memory.scan_target_history.values()
        )

    def unique_scan_target_count(self, memory: "AgentMemory") -> int:
        return len(memory.scan_target_history)

    def append(self, memory: "AgentMemory", status: dict[str, Any], keep: int = 200) -> None:
        memory.kpi_history.append(dict(status))
        if len(memory.kpi_history) > keep:
            memory.kpi_history = memory.kpi_history[-keep:]


class MovementAuditService:
    """Record active-route progress and identify repeated slow movement."""

    def update(self, memory: "AgentMemory", state) -> list[dict[str, Any]]:
        planets = {int(planet.id): planet for planet in state.planets}
        diagnostics = []
        current_ids = set()
        for fleet in state.fleets:
            if fleet.owner != state.player_id or fleet.destination_planet_id is None:
                continue
            target = planets.get(int(fleet.destination_planet_id))
            if target is None:
                continue
            key = str(int(fleet.id))
            current_ids.add(key)
            current_range = math.hypot(
                float(target.position.x) - float(fleet.position.x),
                float(target.position.y) - float(fleet.position.y),
            )
            warp = (
                fleet.destination_warp
                if fleet.destination_warp is not None
                else (fleet.native or {}).get("native_destination_warp")
            )
            task = (
                fleet.destination_task
                if fleet.destination_task is not None
                else (fleet.native or {}).get("native_destination_task")
            )
            prior = memory.fleet_movement_history.get(key)
            row = {
                "fleet_id": int(fleet.id),
                "destination_planet_id": int(fleet.destination_planet_id),
                "year": int(state.year),
                "current_range": round(current_range, 3),
                "commanded_warp": int(warp) if warp is not None else None,
                "native_task": int(task) if task is not None else None,
                "prior_range": None,
                "expected_movement": None,
                "actual_progress": None,
                "suspicious": False,
                "suspicious_slow_turns": 0,
                "flag": None,
            }
            repeated = 0
            if (
                prior
                and int(prior.get("destination_planet_id", -1)) == int(fleet.destination_planet_id)
                and int(prior.get("year", state.year)) < int(state.year)
            ):
                years = int(state.year) - int(prior["year"])
                prior_range = float(prior.get("range", current_range))
                actual = prior_range - current_range
                expected = None
                if warp is not None and int(warp) > 0:
                    expected = min(prior_range, float(int(warp) ** 2 * years))
                suspicious = bool(
                    expected is not None
                    and expected >= 16.0
                    and prior_range > 8.0
                    and actual < expected * 0.35
                )
                repeated = (
                    int(prior.get("suspicious_slow_turns", 0)) + 1
                    if suspicious else 0
                )
                row.update({
                    "prior_year": int(prior["year"]),
                    "prior_range": round(prior_range, 3),
                    "expected_movement": round(expected, 3) if expected is not None else None,
                    "actual_progress": round(actual, 3),
                    "suspicious": suspicious,
                    "suspicious_slow_turns": repeated,
                })
                if repeated >= 2:
                    row["flag"] = (
                        f"SUSPICIOUS MOVEMENT: W{warp} fleet advanced only "
                        f"{actual:.1f} ly versus approximately {expected:.1f} ly expected; "
                        f"slow progress observed {repeated} consecutive times."
                    )
            memory.fleet_movement_history[key] = {
                "fleet_id": int(fleet.id),
                "destination_planet_id": int(fleet.destination_planet_id),
                "year": int(state.year),
                "range": current_range,
                "commanded_warp": int(warp) if warp is not None else None,
                "native_task": int(task) if task is not None else None,
                "suspicious_slow_turns": repeated,
            }
            diagnostics.append(row)

        # Dead/idle fleets are not useful as future comparison baselines.
        for key in list(memory.fleet_movement_history):
            if key not in current_ids:
                memory.fleet_movement_history.pop(key, None)
        return diagnostics


intelligence_memory_service = IntelligenceMemoryService()
action_outcome_service = ActionOutcomeService()
exploration_memory_service = ExplorationMemoryService()
kpi_history_service = KPIHistoryService()
movement_audit_service = MovementAuditService()
