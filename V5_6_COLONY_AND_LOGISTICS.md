
# v5.6 Colony + Logistics

Colonization now attempts the complete observed native workflow:
- rank only known viable worlds;
- load exactly 25 kT of population cargo (2,500 colonists) when the colony fleet is on an owned world with enough population;
- WaypointAdd;
- WaypointChangeTask task 2 = Colonize.

If a colony fleet already carries at least 2,500 colonists, the load block is omitted.

Transports use a deliberately narrow but real observed logistics mission:
- source must have >=10/20/30 kt Ironium/Boranium/Germanium;
- load exactly 10/20/30;
- choose a lower-stocked owned destination;
- task 1 = Transport;
- unload observed pattern: all ironium, 10 boranium, 15 germanium.

Arbitrary cargo quantities are NOT claimed as generalized yet.

Decision reports continue to show Object - Action - Reason and ranked colony alternatives.
