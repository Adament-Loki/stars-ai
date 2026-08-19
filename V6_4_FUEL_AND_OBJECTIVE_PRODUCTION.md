
# v6.4 Fuel-Aware Navigation + Objective Production

## Fuel model
Native fleets now use current fuel, fuel capacity, reconstructed dry mass, cargo mass, installed engine, stock engine fuel-usage values, and IFE/CE traits. Fuel follows the Stars! formula:

`fuel mg = distance LY × mass kt / 200 × FUN / 100`

IFE applies the documented 0.85 multiplier. Routine CE movement is capped at Warp 6. Normal operation is capped at Warp 9.

## Mission safety
- Colony ships budget one-way fuel because colonization consumes the ship.
- Remote miners and freighters reserve fuel for a practical return leg.
- Scouts and combat ships retain operating/return reserve.
- Non-ramscoop fleets below ~15% fuel prefer a reachable owned starbase before a new mission.
- If neither the target nor a refuel base is safely reachable, the AI blocks the move instead of knowingly stranding the fleet.

The report now shows fuel/capacity, reconstructed mass, engine, selected warp, and estimated burn.

Stock reconstruction gives the starter examples:
- Armed Probe: ~23kt, 50mg, Long Hump 6
- Long Range Scout: ~25kt, 300mg, Long Hump 6
- Cotton Picker: ~574kt, 210mg, Long Hump 6

## Objective production
Production is now connected to strategic demand and uses existing designs only.

- More known viable colony worlds than colony assets -> queue additional existing colony ships.
- Large unexplored frontier / high scout objective -> queue more scouts.
- Scout choice is range-aware, preferring Long Range Scout over a short-range armed probe when appropriate.
- Multiple strong remote-mining targets can justify another existing miner design.
- Existing custom ship queue entries are preserved across one-year autoplay invocations.

## Native custom ship queue
StarsAPI documents ProductionQueue `itemType=4` as a custom ship/starbase design. Our controlled X sample also showed:
- design slot 0 x1: `01 00 04 00`
- design slot 1 x1: `01 04 04 00`

v6.4 enables this only for copies of existing ship designs. It does not create new designs.
