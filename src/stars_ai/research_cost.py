"""Resource cost of Stars! technology.

Each level has its own price, and that price rises faster than a straight
line. Reaching a level costs the sum of every step from the current level,
and resources already spent toward the next step carry forward. A race sets
each field to one of three costs: 50% less, standard, or 75% extra. The
player file stores those as 2, 1, and 0. Generalized Research puts half of a
focused budget on the current field.
"""
from __future__ import annotations

import math

FIELDS = ("energy", "weapons", "propulsion", "construction", "electronics", "biotechnology")

# Player fullData bytes 62-67. Confirmed against the built-in AI races:
# all-zero Turindrones and Automitrons are "75% extra" on every field, and
# Macinti's Energy and Construction "50% less" are stored as 2.
_COST_CODE_PERCENT = {0: 175, 1: 100, 2: 50}
_DIRECT_PERCENT = {50: 50, 100: 100, 175: 175}


def _standard_level_costs() -> tuple[int, ...]:
    # Level n at 100% is Fibonacci(n + 4) * 10. Level 1 is 50, level 2 is 80,
    # level 3 is 130, and the series continues through level 26.
    fib = [0, 1]
    while len(fib) < 32:
        fib.append(fib[-1] + fib[-2])
    return tuple(0 if level == 0 else fib[level + 4] * 10 for level in range(27))


STANDARD_TECH_LEVEL_COST = _standard_level_costs()


def field_cost_percent(race, field: str) -> int:
    """Percent of the standard cost for one field.

    Absent race data stays at 100%. A stored 0 is the expensive setting, not
    a missing byte.
    """
    native = getattr(race, "native", {}) or {}
    raw = native.get("research_cost_raw")
    if not raw:
        return 100
    try:
        index = FIELDS.index(str(field))
    except ValueError:
        return 100
    if index >= len(raw):
        return 100
    try:
        value = int(raw[index])
    except (TypeError, ValueError):
        return 100
    if value in _DIRECT_PERCENT:
        return _DIRECT_PERCENT[value]
    return _COST_CODE_PERCENT.get(value, 100)


def field_cost_label(percent: int) -> str:
    """Short description of a field's racial research setting."""
    if int(percent) == 50:
        return "50% less expensive"
    if int(percent) == 175:
        return "75% more expensive"
    return "standard cost"


def level_research_cost(race, field: str, level: int) -> int:
    """Resources to attain `level` in `field` from the previous level."""
    step = int(level)
    if step <= 0 or step >= len(STANDARD_TECH_LEVEL_COST):
        return 0
    return int(STANDARD_TECH_LEVEL_COST[step] * field_cost_percent(race, field) / 100)


def cumulative_field_cost(race, field: str, current: int, target: int) -> int:
    """Sum of every level still ahead in one field.

    Level 3 alone is 130 resources at standard cost. Reaching level 3 from 0
    costs 50 + 80 + 130. A 50% field halves each of those steps; a 75% extra
    field multiplies each of them by 1.75.
    """
    total = 0
    for level in range(max(0, int(current)) + 1, int(target) + 1):
        total += level_research_cost(race, field, level)
    return int(total)


def resources_to_requirements(race, tech, requirements: dict) -> int:
    """Cumulative resources still required to reach every listed field level."""
    total = 0
    for field, target in (requirements or {}).items():
        if field not in FIELDS:
            continue
        current = int(getattr(tech, field, 0) or 0)
        total += cumulative_field_cost(race, field, current, int(target))
    return int(total)


def focused_research_fraction(race) -> float:
    """Share of a research budget that lands on the field being researched.

    Generalized Research keeps half of the budget on the current field and
    spreads the rest across the other five.
    """
    lrts = {str(value).upper() for value in ((getattr(race, "native", {}) or {}).get("lrts") or [])}
    return 0.50 if "GR" in lrts else 1.0


def yearly_research_resources(state, *, allocation_percent: int = 15) -> int:
    """Resources per year that reach the field currently being researched."""
    from .planet_economy import decode_race_economy, estimated_operating_resources

    economy = decode_race_economy(getattr(state, "race", None))
    energy = int(getattr(getattr(state, "tech", None), "energy", 0) or 0)
    owned = [
        planet for planet in getattr(state, "planets", []) or []
        if getattr(planet, "owner", None) == getattr(state, "player_id", None)
    ]
    total = sum(
        int(estimated_operating_resources(planet, economy, energy_tech=energy)["estimated_resources"])
        for planet in owned
    )
    applied = int(total * max(0, int(allocation_percent)) / 100 * focused_research_fraction(getattr(state, "race", None)))
    return max(0, applied)


def research_turns(state, requirements: dict, *, allocation_percent: int = 15) -> tuple[int, int, int]:
    """Return (turns, resource_cost, yearly_budget) for one capability."""
    cost = resources_to_requirements(getattr(state, "race", None), getattr(state, "tech", None), requirements)
    budget = yearly_research_resources(state, allocation_percent=allocation_percent)
    if cost <= 0:
        return 1, 0, budget
    if budget <= 0:
        missing = sum(
            max(0, int(level) - int(getattr(state.tech, field, 0) or 0))
            for field, level in (requirements or {}).items()
            if field in FIELDS
        )
        return max(1, missing), cost, 0
    return max(1, int(math.ceil(cost / budget))), cost, budget
