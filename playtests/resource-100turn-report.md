# 100-turn competitiveness report

Three hosted games, same seed (`5Comps11Total`), seats 1–4 allied and Balanced, dialog watcher on. Each run is a new folder under `playtests/runs`. The seed was not modified.

The team never held the winning position. The third game is the best of the three: P5’s observer lead shrank from 34.6% to 25.0%, and Player 3 finished third, ahead of Player 7.

Observer score, which is what the standings use, is planets×100 + population/10,000 + factories×1.5 + mines×0.4 + ships×8 + tech×35 + starbases×40. Factories decide it. Player 5 wins that term. Player 6 wins planets, population, ships, and bases, and finishes second.

## Standings at year 2500

| | Run 1 | Run 2 | Run 3 |
| --- | ---: | ---: | ---: |
| P5 lead | 34.6% | 38.9% | 25.0% |
| Best allied place | 4th (P2) | 4th (P3) | 3rd (P3) |
| P1 planets / resources | 30 / 11,837 | 17 / 13,750 | 27 / 12,422 |
| P2 planets / resources | 24 / 11,755 | 23 / 13,004 | 24 / 11,723 |
| P3 planets / resources | 21 / 10,064 | 13 / 10,897 | 20 / 12,141 |
| P4 planets / resources | 13 / 5,026 | 15 / 5,156 | 13 / 5,880 |
| P5 resources | 55,532 | 59,119 | 56,033 |
| P6 planets / resources | 158 / 51,338 | 163 / 80,360 | 174 / 50,071 |

Resources here are the race formula: population resources plus resources from operating factories. Player 5 is past 50,000. Our best seat in any run is 13,750.

## What the logs showed

Run 1. Player 1 owned 13 worlds under 30% habitability and 20 worlds under 50,000 colonists. Colony orders such as Zarquon at 9% current habitability scored 93 because mineral concentrations of 100 added 105 points, more than the habitability term. Every new colony loaded 2,500 colonists and left 900,000 on the source. No journal line in any of the four seats said “strategic population export” was impossible; shared transports did move people, but only to the five designated hubs. Primary objective was defend for 37 to 60 turns. Border raids: zero. Research focus stayed on weapons. At year 2499 Player 1 was still on Weapons 22 while the tech total was 43.

Run 2. Feeding every undergrown world worked. Player 1’s worlds under 50,000 colonists fell from 20 to 6, and resources rose to 13,750. The hard ban on settling below 30% current habitability did the opposite for territory: Player 1 fell from 30 worlds to 17, Player 3 from 21 to 13, and Player 6’s population resources rose to 80,360. Player 4’s log at year 2479 says the fleet is outmatched (readiness 2,453, below the 1.09× line) and then orders every combat ship to stack on Foresight. That stack, and the ones after it, died: 17 ships in 2480, 23 in 2490, 6 in 2494.

Run 3. Minerals are capped at 20 points and a low current habitability is penalized, so a 30% world ranks ahead of a rich 10% world. A poor world is still settled when nothing better is in range. Planets came back (Player 1 to 27, Player 3 to 20) and the observer gap narrowed to 25%. Player 3 captured planet 136 from Player 7 and finished third. The retreat order did fire (“fall back to Gold instead of stacking at Foresight”). Player 4 still lost 16 ships on turn 94, because a different order thought the stack met the 1.09× line and concentrated anyway.

## Changes made tonight, ranked

**Highly effective.** Every owned world under 25% of capacity, with habitability of at least 30, now gets a population parent, not only the five Layer-1 hubs. In the run where this was the main growth change, Player 1’s underfed worlds went from 20 to 6 and resources from 11,837 to 13,750.

**Effective.** Colony ranking no longer lets a perfect mineral report outrank current habitability, and a livable world is still taken when every better claim is gone. That combination recovered the planets the hard ban had given away and produced the narrowest score gap and the only third-place finish.

**Somewhat effective.** An outmatched patrol falls back to another colony instead of stacking on the threatened world. The order is in Player 4’s journal, and the triple wipe of the second run was not repeated in that form. The 1.09× “we are strong enough” path still stacked ships and Player 4 still lost 16 of them.

**Limited.** Remembering yearly resources, scoring colonies by race resource value, keeping expansion level with development while the factory cap is under 50,000, and printing the race’s resource rates on factory queues. All three games played under that doctrine. The best seat stayed near 12,000–14,000 resources.

**Limited.** Border attacks ignore unseen planets when deciding the frontier is full, and only a high-value world is secured. Captures went from zero in the first game to one in each later game. That did not move a seat into the lead.

**Counterproductive.** Refusing to colonize any world under 30% current habitability after turn 40. Player 1 dropped from 30 worlds to 17 and Player 3 from 21 to 13. The observer gap widened from 34.6% to 38.9%. The third run removed that ban.

## Fifteen changes still worth making

These are from the turn journals and the year-2500 planet lists, not from a general wishlist.

1. **First freighter after a colony is people, not minerals.** Journals show transports to Scotch and Arafat carrying ironium while new worlds sit at 2,500 colonists. A world under 25% of capacity should outrank every mineral reserve until it gets one full hold.

2. **Do not open another colony while many owned worlds are under 25% capacity.** Player 1 in the third run had 27 worlds, 15 of them under 50,000 people, and a factory cap of only 18,045 resources. Player 5 had 31 worlds, none under 50,000, and a cap of 80,353. Filling the worlds we have is the 50,000-resource path.

3. **Factories until the operable cap, on any world past 25% population.** From turn 75 to 100, Player 5 added about 18,000 factories. Player 1 added about 2,900. The queues still mix mines, a few factories, and terraform. The score is factories×1.5.

4. **Spend Germanium on factories once the world has the population.** Queue lines say “3 factories while retaining 8 kT Germanium.” That floor is why a developed world adds a handful of factories a year.

5. **Stop calling 1.09× of the visible fleet “enough.”** Player 4 concentrated on Gold and Foresight whenever the estimate cleared that line, then lost the stack at the same coordinates (2031, 1568 and 2089, 1703) across games. Remember a hex that destroyed a fleet and do not send the next one there without a much larger surplus.

6. **A patrol contact inside 135 ly should not make defend the empire objective.** Player 4 was on defend for most of 100 turns because of “19 territorial violations,” including transports. That retasked yards and fleets while the empire had 5,000 resources.

7. **Give research a small permanent budget on the cheapest unfinished field.** Player 1 and Player 2 finish with tech totals around 40, still aimed at Weapons 14–22, with the sprint deferred because no world had spare resources. Player 3 reached 78 and was the only seat to finish third. Tech is 35 points a level, and weapons-only leaves five fields behind.

8. **Build the follow-up colonizer cargo we already designed.** The journal records a Privateer colonizer whose cargo goes from 25 kT to 250 kT, then deletes the general hull and keeps launching 2,500 colonists. The 250 kT hull is the difference between a seed and a breeder.

9. **After the factory cap, build freighters and colony ships.** Player 2 ended the third run on its factory cap with 15 ships. Player 5 added 270 ships in the last 25 turns. Idle resources after the cap are being left on the ground.

10. **One cheap base on every world past 100,000 people.** Player 5 finished with 29 bases, Player 6 with one per planet. Our seats finished with 6 to 13. Range and the 40-point base term both suffer, and the quiet-border cheap design already exists.

11. **Assign the outward arc to one seat.** Allied space is correctly treated as a soft border, but all four seats still colonize the pocket and the near rocks. Player 6 took 174 worlds, including the far map our scouts had already seen.

12. **Raid the border once every visible claim is dead.** At the end of the third run Player 1 had four unowned worlds in supply range and all four were uninhabitable, while Player 6 held 174. The capture rule fired once per game. A dead local frontier should release one combat fleet onto the nearest enemy world.

13. **Do not secure a world we cannot grow.** A 9% world is 100 observer points and almost no factories for the rest of the century. Take it only when the alternative is giving it to Player 6, and do not feed it population that a 60% world still needs.

14. **Terraform the worlds we keep before researching another weapon tier.** Colony logs show 16 to 25 terraform steps still required on worlds we just settled, while the research target is the next torpedo. Habitability is the population cap. Population is the factory cap.

15. **Player 4 needs a different loss rule than the other three.** Across all three games that seat is last, loses planets on the last turns, and loses fleets at the same two battle sites. Its habitability spread is the worst (only four worlds at 55% or better in the third run) and its combat ships keep flying back to the site of the last defeat. Hold those worlds with defenses and mines, and keep the ships one hop behind the front.
