
# v5.5 Colonization Priority

Colonization is now explicitly ranked and explained.

For each colony fleet, the AI evaluates every observed, unowned planet with
estimated habitability >= 25%.

Candidate score currently weighs:
- habitability most heavily,
- travel distance from the actual colony fleet,
- modest frontier-expansion value,
- known resource information as a small tie-breaker.

The decision report includes:

```
COLONIZATION PRIORITY

Santa Maria - SELECT Rigel - Selected Rigel as highest-ranked colony candidate...

  #1 Rigel - score 76.2 - habitability 85%; fleet distance 25.0; ...
  #2 Vega  - score 62.5 - habitability 90%; fleet distance 120.0; ...
  #3 Deneb - score 35.8 - habitability 40%; fleet distance 15.0; ...
```

This makes the "why Planet X over Planet Y?" decision directly inspectable.

Colony repositioning is given high strategic priority (110), so early colony
fleets should move toward the best candidate rather than remain idle.

Important native limitation:
- validated simple movement to the colony target: enabled
- final Stars! Colonize waypoint task: still blocked until captured from a
  controlled Stars!-generated `.x#` sample

The report states this explicitly each turn so strategic selection and native
execution are never conflated.
