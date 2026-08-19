
# Decision Tracing v2.6

The agent now supports structured decision traces intended to explain *why* it acted.

## Trace contents

Each event can record:

- decision category (strategy, research, colonization, diplomacy, combat, defense, production)
- final decision
- selected candidate
- plain-English reason
- goals affecting the decision
- hard rules affecting the decision
- state/context used
- every considered candidate
- each candidate's score
- individual score factors, weights, and contributions
- disqualification reasons

## Outputs

`DecisionTrace.write_text()` produces a readable diagnostic log.

`DecisionTrace.write_json()` produces structured data suitable for a UI/dashboard or later analysis.

Example:

```
[1] RESEARCH: Choose research field
Selected: Propulsion
Why: Best support for current expansion goals.

Goals:
  - Reach Propulsion 8
  - Explore 60% of galaxy

Candidates:
  - Propulsion: 2.150
      unlock_value: value=0.900, weight=1.500, contribution=1.350
      persona_fit: value=1.000, weight=0.800, contribution=0.800
  - Weapons: 1.020
      unlock_value: value=0.700, weight=1.000, contribution=0.700
      persona_fit: value=0.400, weight=0.800, contribution=0.320
```

Hard-rule failures remain visible:

```
Ally Player 3: DISQUALIFIED (Human players cannot be allied with)
```

This makes AI behavior explainable without a debugger and gives a stable event stream for a future Windows host dashboard.
