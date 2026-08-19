
# v4.7 Human-Readable Omniscient Observer

The Windows autoplay runner now reads the `.hst` file after every generated turn.

At checkpoint turns (default 10, 25 and 50) it writes:

```
checkpoints\turn-010\OBSERVER_REPORT.txt
checkpoints\turn-025\OBSERVER_REPORT.txt
checkpoints\turn-050\OBSERVER_REPORT.txt
```

It also keeps:

```
LATEST_OBSERVER_REPORT.txt
observer\turn-000.json
observer\turn-001.json
...
observer\turn-050.json
```

The report answers:

- Who is currently leading?
- Is the lead narrow or decisive?
- Who is in last place?
- Has player-vs-player fighting apparently started?
- Which planets changed hands?
- Who suffered major fleet losses?
- Who leads territory, population, industry, fleet mass and technology?
- How has every player changed since the previous checkpoint?
- Is anyone materially falling behind or eliminated?
- What is observable about the SS and SD players?

## Important interpretation

Planet ownership changing directly from one player to another is treated as
strong evidence of conquest.

A large sudden net fleet loss is reported as likely combat, but the observer
labels it carefully because scrapping/merging can also affect fleet totals
until Stars! battle blocks are fully decoded.

The diagnostic "observer score" is not Stars!' built-in score. It is a
transparent composite used only to establish a useful leaderboard.
