
# v6.1 Friend Relations / Reciprocal Alliances

Controlled native sample:
- acting player: Player 2
- target: Player 1
- relation selected: Friend
- Type 38 PlayersRelationChange payload: `01 00`

StarsAPI documents relation values:
- 0 Neutral
- 1 Friend
- 2 Enemy

The observed Friend block is implemented as:
`[relation=1, target_player_index=player_id-1]`

Only Friend writes are enabled. Neutral/Enemy remain disabled until separately
sampled.

## JSON

```json
"allied_pairs": [
  [1, 2]
]
```

Pairs are reciprocal automatically:
- P1 marks P2 Friend
- P2 marks P1 Friend

This is intended to establish the mutual Friend state required for shared map /
intel behavior.

## Combat safety

Configured allies are treated as Friend before military planning on the same
turn the native relation order is emitted. The AI therefore cannot decide to
attack a player it is simultaneously establishing as an ally.

Once the M-file reports Friend, native relation state itself suppresses military
targeting.

## Diagnostics

Decision reports include a DIPLOMACY section:
- FRIEND PENDING
- FRIEND ACTIVE
- native relation-order emission status
