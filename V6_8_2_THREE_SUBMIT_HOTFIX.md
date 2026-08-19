
# v6.8.2 — Three-Block Save+Submit Hotfix

A second controlled manual Stars! test isolated the remaining Player 2 corruption.

## Controlled comparison

Manual clean `GAME(5).x2` contains:

```text
FileHeader: submitted X
FileHash: order length = 0x34 = 52 bytes
Type 4   WaypointAdd          len 12
Type 29  ProductionQueue      len 14
Type 34  ResearchChange       len 2
Type 46  SaveAndSubmit        len 4   01 01 05 19
Type 46  SaveAndSubmit        len 4   01 01 05 19
Type 46  SaveAndSubmit        len 4   01 01 05 19
Footer
```

The gameplay blocks are byte-for-byte the same as the AI-generated turn.

The previous AI file had only one Type 46. v6.8.1 changed this to two based on the
first manual sample, but the complete newly supplied Save+Submit control proves
the actual transaction used here contains THREE Type 46 blocks.

## Resulting M-file evidence

The bad hosted M2 had header flags:

```text
0xA0
```

The clean manually submitted successor M2 has:

```text
0x03
```

Both M files are structurally parseable and contain the processed gameplay
orders. This strongly ties the client-visible corruption to submit/turn-state
registration rather than to waypoint, production, or research bytes.

## v6.8.2 behavior

When the known-good template supplies the validated Type46 payload, autonomous
Save+Submit now emits exactly three consecutive copies:

```text
46: 01 01 05 19
46: 01 01 05 19
46: 01 01 05 19
```

FileHash is then rebuilt across the complete stream.

For the controlled Player 2 order shape this produces exactly:

```text
FileHash order length = 52 (0x34)
```

matching `GAME(5).x2`.

Fresh per-turn salt and scout-target deconfliction from v6.8.1 remain enabled.
