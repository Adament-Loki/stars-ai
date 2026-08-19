# Stars! AI Persona / Macro Objective System

The persona layer sits between native game-state ingestion and tactical order generation.

```
.m#/.xy -> PlayerState -> GameState -> Persona -> StrategicPlan -> strategy modules -> OrderSet
```

A persona does **not** manipulate Stars! files directly. It declares strategic objectives and posture. The research, economy, exploration, and military modules consume that plan.

## Built-in personas

- `BalancedPersona` — neutral baseline.
- `ExpansionistPersona` — scouting, colonization, propulsion, logistics; accepts lower-value colonies and more frontier risk.
- `IndustrialistPersona` — factories, mines, construction, logistics; conservative combat posture.
- `TechnologistPersona` — research-heavy, especially electronics and balanced enabling tech.
- `MilitaristPersona` — weapons/construction/energy, defense and attack, higher combat risk tolerance.

## StrategicPlan

Each turn the persona produces:

- objective weights: expand, develop, research, defend, attack, scout, logistics
- research-field weights
- fleet-mission weights
- planet-development weights
- risk tolerance
- minimum acceptable colonization value
- scouting aggressiveness
- defensive response radius
- combat strength threshold

The plan also reacts modestly to the live game state. For example, nearby hostiles increase defense, weapons/energy research, and planetary defense pressure without replacing the underlying persona.

## Usage

```python
from stars_ai.agent import StarsAgent
from stars_ai.persona import ExpansionistPersona

agent = StarsAgent(state, persona=ExpansionistPersona())
orders = agent.play_turn()
print(agent.last_plan.to_dict())
```

Or by name:

```python
from stars_ai.persona import persona_from_name
persona = persona_from_name("militarist")
```

## Next evolution

The current personas influence heuristics. The next layer should add explicit multi-turn Goals (for example, "colonize 5 worlds", "reach Propulsion 8", "establish a defended border", "field 3 combat fleets") and a progress evaluator that keeps those goals alive across turns in AgentMemory.

## Explicit multi-turn goals

Personas can carry concrete goals in addition to their baseline personality:

```python
from stars_ai.persona import ExpansionistPersona
from stars_ai.goals import ReachTechGoal, OwnPlanetsGoal, ExploreGalaxyGoal

persona = ExpansionistPersona().with_goals(
    ReachTechGoal("propulsion", 8, priority=1.5),
    OwnPlanetsGoal(10, priority=1.2),
    ExploreGalaxyGoal(0.60, priority=1.0),
)

agent = StarsAgent(state, persona=persona)
orders = agent.play_turn()
```

Current goal classes:

- `ReachTechGoal(field, target_level)`
- `OwnPlanetsGoal(target_count)`
- `ExploreGalaxyGoal(target_fraction)`
- `IndustrialCapacityGoal(target_factories, target_mines)`

Every goal reports 0..1 progress, modifies the macro plan while incomplete, and records progress in `AgentMemory.goal_progress` so the host/controller can show long-term status.

## Diplomacy and conflict selection

The persona layer now includes a `DiplomacyPolicy` that evaluates every known player as
`hostile`, `neutral`, `helpful`, or `allied`. It tracks explicit Stars! relations, relative
visible fleet strength, border pressure, trust, threat, usefulness, and conflict reward/risk.

Human-player slots are configured by the host/controller, for example:

```python
persona = BalancedPersona().with_human_players(2, 5)
```

**Hard rule:** human players can never be allied. They may be treated as helpful and the AI may
choose coexistence/cooperation or avoidance, but `can_ally()` is always false and the agent will
never emit a `friend` relation intention for those slots.

AI-controlled players can become alliance candidates if trust is high and threat is low.
Diplomatic changes currently emit normalized `set_player_relation` intentions only; native
`PLAYERS_RELATION_CHANGE` serialization remains intentionally disabled until its `.x#` payload
is validated empirically because StarsAPI leaves that block's encode/decode as TODO.
