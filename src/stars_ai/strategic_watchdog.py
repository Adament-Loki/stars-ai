
from __future__ import annotations

import math
from typing import Any


# Through Turn 25, galaxy percentages are intentionally NOT the planning basis.
# These are explicit accomplishments expected from an opening empire.
OPENING_HARD_MILESTONES = [
    # turn, minimum explored, optimal explored, minimum NEW colonies, optimal NEW colonies
    (5,   5, 10, 1,  2),
    (10, 10, 25, 3,  5),
    (15, 20, 35, 5,  8),
    (25, 35, 50, 8, 12),
]

# After Turn 25, exploration coverage begins to scale with galaxy size.
# Colony goals remain hard counts because viable-world density is race/game dependent.
MIDGAME_COVERAGE_MILESTONES = [
    # turn, minimum explored %, optimal explored %, minimum NEW colonies, optimal NEW colonies
    (40, 0.45, 0.55, 12, 16),
    (55, 0.60, 0.67, 16, 20),
    (75, 0.70, 0.80, 20, 25),
]


def _next_milestone(turn: int, total_planets: int) -> dict[str, Any]:
    if turn <= 25:
        row = next((x for x in OPENING_HARD_MILESTONES if turn <= x[0]), OPENING_HARD_MILESTONES[-1])
        deadline, emin, eopt, cmin, copt = row
        return {
            "mode": "hard_numbers",
            "deadline_turn": deadline,
            "explored_min": emin,
            "explored_optimal": eopt,
            "new_colonies_min": cmin,
            "new_colonies_optimal": copt,
        }

    row = next(
        (x for x in MIDGAME_COVERAGE_MILESTONES if turn <= x[0]),
        MIDGAME_COVERAGE_MILESTONES[-1],
    )
    deadline, pmin, popt, cmin, copt = row
    return {
        "mode": "coverage_percentage",
        "deadline_turn": deadline,
        "explored_min": math.ceil(total_planets * pmin),
        "explored_optimal": math.ceil(total_planets * popt),
        "explored_min_percent": round(pmin * 100, 1),
        "explored_optimal_percent": round(popt * 100, 1),
        "new_colonies_min": cmin,
        "new_colonies_optimal": copt,
    }


def _pressure(value: int, minimum: int, optimal: int, turns_left: int) -> float:
    if value >= optimal:
        return 0.85
    if value >= minimum:
        return 1.0

    gap = minimum - value
    # Escalate as a hard deadline approaches.
    if turns_left <= 0:
        return 2.0
    required_per_turn = gap / max(1, turns_left)
    if turns_left <= 3 or required_per_turn >= 2.0:
        return 1.75
    if turns_left <= 6 or required_per_turn >= 1.0:
        return 1.45
    return 1.20


def evaluate_strategic_watchdog(state, memory) -> dict[str, Any]:
    turn = max(0, int(state.year) - 2400)
    total = max(1, len(state.planets))
    explored = int(memory.ever_observed_count())
    new_colonies = int(memory.new_colonies_count())
    owned = sum(1 for p in state.planets if p.owner == state.player_id)
    milestone = _next_milestone(turn, total)
    turns_left = int(milestone["deadline_turn"]) - turn

    status = {
        "year": int(state.year),
        "turn": turn,
        "total_planets": total,
        "explored_count": explored,
        "explored_percent": round(explored * 100.0 / total, 1),
        "new_colonies": new_colonies,
        "owned_planets": owned,
        "discoveries_last_5_turns": int(memory.discoveries_in_recent_years(state.year, 5)),
        "scan_orders_total": int(memory.scan_order_count()),
        "unique_scan_targets_total": int(memory.unique_scan_target_count()),
        "milestone": milestone,
    }

    status["exploration_pressure"] = _pressure(
        explored,
        int(milestone["explored_min"]),
        int(milestone["explored_optimal"]),
        turns_left,
    )
    status["colonization_pressure"] = _pressure(
        new_colonies,
        int(milestone["new_colonies_min"]),
        int(milestone["new_colonies_optimal"]),
        turns_left,
    )
    status["exploration_below_minimum"] = explored < int(milestone["explored_min"])
    status["colonization_below_minimum"] = new_colonies < int(milestone["new_colonies_min"])

    if status["scan_orders_total"] > 0:
        status["scan_target_reuse_ratio"] = round(
            max(0, status["scan_orders_total"] - status["unique_scan_targets_total"])
            / status["scan_orders_total"],
            3,
        )
    else:
        status["scan_target_reuse_ratio"] = 0.0

    notes = []
    if milestone["mode"] == "hard_numbers":
        notes.append(
            f"Opening hard goal by T{milestone['deadline_turn']}: "
            f"explore >= {milestone['explored_min']} (optimal {milestone['explored_optimal']}), "
            f"create >= {milestone['new_colonies_min']} new colonies "
            f"(optimal {milestone['new_colonies_optimal']})."
        )
    else:
        notes.append(
            f"Coverage goal by T{milestone['deadline_turn']}: "
            f"explore >= {milestone['explored_min_percent']}% "
            f"(optimal {milestone['explored_optimal_percent']}%); "
            f"create >= {milestone['new_colonies_min']} new colonies."
        )

    notes.append(
        f"Actual: explored {explored}/{total} ({status['explored_percent']}%), "
        f"new colonies={new_colonies}, owned={owned}, "
        f"new discoveries last 5 turns={status['discoveries_last_5_turns']}."
    )

    if turn >= 5 and status["discoveries_last_5_turns"] == 0 and explored < total:
        notes.append("STAGNATION: zero new planets discovered in the last five turns.")
        status["exploration_pressure"] = max(status["exploration_pressure"], 1.75)

    if (
        status["scan_orders_total"] >= 10
        and status["scan_target_reuse_ratio"] > 0.25
    ):
        notes.append(
            f"SCOUT EFFICIENCY WARNING: {status['scan_target_reuse_ratio']:.0%} "
            "of persistent scan assignments reused a previous target."
        )

    status["notes"] = notes
    return status
