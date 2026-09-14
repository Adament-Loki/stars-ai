# STARS! AI Proactive Force Capability Patch

Baseline reviewed: `Adament-Loki/stars-ai` main at commit `880fc7cfdf40621449fc837aeb1c9b8b98610cf5`.

This patch is designed to apply **after** the earlier scout-splitting and remote-mining-pressure patches.

## Changes

- Adds an explicit **Border Combat Seed** design when border/minefield pressure exists but the empire has no combat blueprint.
- Adds **800+ kT capture transport capability demand** for observed population-capture targets. The existing capture execution safeguards remain in force.
- Adds a **Territorial Strike Bomber** design demand for observed hostile/opposed colonies inside the AI's claim radius, plus a limited 2–6 bomber production demand once the design exists.
- Makes minelayer doctrine more pre-emptive:
  - transport/armed-transport traffic gets a lower minefield trigger;
  - significant hostile traffic in a 1.35x outer warning ring can seed an edge minefield before it enters claimed space;
  - hostile colonies inside claimed space create likely transport-corridor minefield anchors;
  - desired territorial minelayers can scale up to 4 instead of 2.
- Neutral scouts and friendly/allied colonies do not trigger bomber or minefield escalation.

## Diagnostics

Adds or extends:

- `state.native["proactive_force_capability_demand"]`
- `state.native["capture_transport_capability_demand"]`
- `state.native["bomber_capability_demand"]`
- `state.native["territorial_defense"]`

## Apply

From the root of your local `stars-ai` checkout:

```bash
python /path/to/stars-proactive-force-capability-patch/apply_patch.py
```

Then validate:

```bash
python -m compileall -q src/stars_ai
pytest -q tests/test_proactive_force_capabilities.py
pytest -q
git diff -- \
  src/stars_ai/design_development.py \
  src/stars_ai/territorial_defense.py \
  src/stars_ai/objective_production.py \
  tests/test_proactive_force_capabilities.py
```

## Playtest focus

1. Border pressure with no combat design should produce `Border Combat Seed`.
2. A capture target requiring 800+ kT should request a large transport design even when ordinary freight backlog is low.
3. A hostile colony inside claimed territory should create bomber capability demand; neutral/allied colonies should not.
4. A hostile transport just outside the formal claim radius should create proactive edge minefield demand.
