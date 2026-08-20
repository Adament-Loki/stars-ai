# v8.0 aggressive terraforming expansion

The opening strategy now targets roughly 15-22 owned planets by Turn 25 on a
normal two-planet start, with 25 planets remaining an aspirational outcome when
the map and race provide enough viable worlds.

- Colony value includes current habitability, the best value reachable with
  current terraforming technology, and discounted long-term potential.
- Standard terraforming uses the exact Energy/Weapons/Propulsion plus
  Biotechnology thresholds. Total Terraforming uses its Biotechnology-only
  thresholds and reduced resource cost.
- Owned planets with a current, beneficial terraforming step queue the native
  `Max Terraform (Auto Build)` production item.
- Turn-25 goals rise to 13 minimum and 20 optimal new colonies. The colony
  pipeline can scale to 5 hulls through Turn 5, 7 through Turn 15, and 10
  through Turn 25, with additional pressure allowance when behind schedule.
- Opening source reserves are 10,000 colonists through Turn 10 and 25,000
  through Turn 25. Multiple same-turn loads now share one population budget.
- Every in-flight colony destination is a reserved claim, preventing idle
  colony fleets from selecting the same planet.
- Colony and scout ranking explicitly favor the home region during the first
  25 turns.
- Once environmental data has been observed, that planet never returns to the
  scout target pool. Persistent observations remain usable for colonization.

Decision reports expose current, current-tech, eventual, and planning
habitability values plus distance from the homeworld so the new choices can be
audited turn by turn.
