
# Stars! Rules Engine v3.0

This layer separates hard/empirical game mechanics from strategy heuristics.

## Modules

- `rules/turn_order.py`
- `rules/population.py`
- `rules/fuel.py`
- `rules/gating.py`
- `rules/minefields.py`
- `rules/packets.py`
- `rules/salvage.py`
- `rules/engine.py`

## Key principle

Strategy planners should ask the Rules Engine for mechanics instead of
re-implementing formulas.

Examples:

```python
rules.overgate(...)
rules.population_growth(...)
rules.population_policy(...)
rules.minefield_warp(...)
rules.packet_decay(...)
rules.scrap_return(...)
```

## Confidence model

Some mechanics are treated as deterministic:
- turn ordering
- fuel rule-of-thumb formula
- packet travel distance
- packet overhead
- salvage/scrap fractions
- documented overgating damage formula

Some are explicitly empirical/heuristic helpers:
- minefield optimal transit warp
- overgate disappearance risk proxy
- breeder population policy

These empirical helpers should remain visible in debug traces and should be
validated further through testbeds/self-play.
