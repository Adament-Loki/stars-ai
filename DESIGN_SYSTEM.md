# Stars AI design system v1.4

The design system now separates three independent questions:

1. **Hull-slot legality** — does a component category physically fit this hull slot, and is quantity within capacity?
2. **Research availability** — do the player's six live tech levels meet the component's Energy / Weapons / Propulsion / Construction / Electronics / Biotechnology requirements?
3. **Race/special availability** — raw PRT, LRT and Mystery Trader masks are extracted from the PLAYER block and exposed for progressively stricter special-item filtering.

## Live player state

`stars_ai.player_tech.player_race_tech_from_file("AI.m1")` reads the owning PLAYER record from an actual `.m#` file. For the current AI test game it decodes tech as `3/3/3/3/3/3`.

## Stock component database

The system parses standard/custom Stars! MOD files through `stars_ai.standard_mod`. Run `python scripts/fetch_stock_mod.py` once to place the canonical stock `UNEDITED.MOD` beside the package, or point `PlayerDesignSystem.from_files()` at another compatible MOD file.

No network request occurs during normal design validation.

## Query example

```python
from stars_ai.design_system import PlayerDesignSystem

system = PlayerDesignSystem.from_files("AI.m1", "src/stars_ai/UNEDITED.MOD")

# Scout hull ID 4, scanner slot index 1
for component in system.legal_components(4, 1):
    print(component.name, component.tech_required)
```

The returned list contains only components that both fit that slot and are researched by this exact player.

## Validation before writing an .x#

Use `system.validate(hull_id, components)` before any DesignChange writer operation. A design should never be serialized if validation fails.
