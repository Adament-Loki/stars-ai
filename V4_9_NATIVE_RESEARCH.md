
# v4.9 Native Research Selection

v4.9 adds native `ResearchChange` serialization to autoplay.

Controlled empirical samples established:

```
Electronics     -> 0F 64
Biotechnology   -> 0F 65
```

The field byte is therefore mapped as:

```
Energy          0x60
Weapons         0x61
Propulsion      0x62
Construction    0x63
Electronics     0x64
Biotechnology   0x65
```

The first byte `0x0F` is preserved exactly from the controlled Stars! samples.
Its complete internal bit semantics remain unknown, so the writer currently
supports only the observed normal `100%` research-field-selection form.

The strategy engine can now make an educated research choice and the native
writer will actually submit it to Stars!.

Recommended validation:
- run 5-10 turns,
- inspect `player-XX-decision-native.json`,
- verify `set_research` appears under `native_result.emitted`,
- open a player turn in Stars! and confirm the research field matches the AI choice.
