# Stars! AI Integrated Agent v4.0

v4.0 consolidates the previous rules, personas, diplomacy, territorial value,
combat doctrine, design lifecycle, observer, and self-play work and adds all ten
items from the post-v3 gap analysis.

## 1. Battle simulator
`battle_simulator.py`
- 10x10 board abstraction
- max 16 rounds
- ship-design stacks/tokens
- beam / torpedo / capital missile / sapper distinction
- initiative
- range and beam dissipation
- missile accuracy vs computer/jammer proxy
- shields before armor
- capital-missile armor bonus
- historical attractiveness targeting
- starbase +1 range
- battle-plan movement abstraction

This is useful predictive combat modeling, but is not yet claimed bit-exact with Stars!.

## 2. Empire population/economy optimizer
`empire_optimizer.py`
- breeder/developing/industrial/mature/exporter roles
- empire-wide donor/receiver scoring
- freighter-aware transfer recommendations

## 3. Starbase/gate network strategy
`base_network.py`
- fortress
- gate hub
- shipyard
- frontier base
- economic base
- connectivity/exposure/industry scoring

## 4. Bombing/invasion planner
`invasion.py`
- capture / neutralize / bomb-or-bypass / bypass
- troop requirement
- bomber and escort priority
- post-capture holdability

## 5. Fleet logistics
`logistics.py`
- direct
- refuel
- gate
- reduced warp
- delay
- future tanker hook

## 6. Counter-design doctrine
`counter_design.py`
- identifies missile/chaff, missile-heavy, beam-heavy, mixed threats
- produces desired counter-design characteristics

The existing design legality/lifecycle system remains responsible for making
candidate designs legal and freeing scarce design slots.

## 7. Intelligence uncertainty
`intelligence.py`
- last-seen age
- confidence decay
- low/high estimated ranges
- conservative enemy strength

## 8. PRT race doctrine
`race_doctrine.py`
Dedicated modifiers for HE, SS, WM, CA, IS, SD, PP, IT, AR, JOAT.

## 9. Native action safety
`native_capabilities.py`
Every semantic action is labeled VALIDATED, PARTIAL, or BLOCKED.
Unvalidated concepts can be planned/logged but should not be emitted as native bytes.

## 10. Multi-turn strategic lookahead
`strategic_lookahead.py`
Compares attack, modernization, consolidation, and future options with discounted
future value, territorial/fleet loss, economic/tech gain, time and risk.

## Integration
`v4_coordinator.py` runs above the existing planners.
`StarsAgent.play_turn()` now invokes v4 assessment after the proven planners.

## Remaining fidelity work
v4.0 implements all ten architectural capabilities, but several are deliberately
conservative rather than pretending exactness:

- battle simulation still needs exact MOD component tables and more exact movement/targeting
- population growth should ultimately use fully ported racebuilder habitability/capacity math
- native packet, relation, battle-plan, brand-new design, and generalized specialized waypoint writers remain blocked/partial
- counter-design generation produces doctrine/specifications; exhaustive legal design enumeration + simulation tournament is a later refinement
- long-horizon lookahead is a scored forward model, not a full game-tree search

These limitations are surfaced explicitly so self-play can distinguish strategic failures from unsupported native actions.


## v4.1 routing refinement
Planet routing now values instant starbase refueling and concealment from non-penetrating scanners.
