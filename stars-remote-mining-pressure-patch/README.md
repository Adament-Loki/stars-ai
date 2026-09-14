# STARS! AI Remote Mining Pressure Patch

Baseline reviewed: `Adament-Loki/stars-ai` commit `880fc7cfdf40621449fc837aeb1c9b8b98610cf5`.

## Behavior change

- Rich remote-mining targets alone no longer cause miner production.
- Remote miners require a mature empire plus a meaningful mineral reserve deficit.
- Mineral pressure uses the existing `working_mineral_reserve()` economic policy for owned worlds.
- Diagnostics are written to `state.native["remote_mining_build_policy"]`.
- Normal races scale to a modest miner force only after pressure is real.
- Alternate Reality still obeys the pressure gate, but after T30 it may scale to a 6–16 ship miner fleet.
- Late-game AR can treat its homeworld as a preferred remote-mining target; normal races remain restricted to observed unowned worlds.

## Thresholds

- `REMOTE_MINING_MIN_TURN = 20`
- `REMOTE_MINING_LATE_TURN = 30`
- `REMOTE_MINING_REAL_PRESSURE_RATIO = 0.25`
- `REMOTE_MINING_SEVERE_PRESSURE_RATIO = 0.50`

These are deliberately named constants so playtest results can tune them easily.

## Apply

From the root of your local `stars-ai` checkout:

```bash
python /path/to/stars-remote-mining-pressure-patch/apply_patch.py
python -m compileall -q src/stars_ai
pytest -q tests/test_remote_mining_pressure_policy.py
pytest -q
git diff -- src/stars_ai/objective_production.py src/stars_ai/fleet_intent.py tests/test_remote_mining_pressure_policy.py
```

This patch does **not** change native Type-5 remote-mining serialization. It changes only miner build policy and target selection.
