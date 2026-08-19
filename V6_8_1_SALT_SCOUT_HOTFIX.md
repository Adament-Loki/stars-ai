
# v6.8.1 — X Lifecycle + Scout Deconfliction Hotfix

## Why this hotfix exists

A Player 2 turn appeared corrupt after hosting. A manually generated
Save+Submit X file for the same turn proved that the semantic gameplay orders
were correct:

- scout waypoint
- production queue
- research change

The manually generated Stars! X differed in X lifecycle metadata:

- Stars! used a fresh X encryption salt rather than the bootstrap-template salt.
- The observed manual Save+Submit transaction contained two consecutive Type 46
  blocks with the same validated `01 01 05 19` payload.
- FileHash correctly included both Type 46 blocks.

## Fresh X salt

v6.8.1 generates a fresh legal 11-bit salt for every emitted X file.

It explicitly avoids:
- the persistent bootstrap-template X salt
- the current M-file salt

The complete encrypted X payload is generated from that fresh header.

## Save + Submit lifecycle

If the known-good X template contains the validated Type 46 payload, the
autonomous Save+Submit transaction emits two consecutive copies, matching the
new controlled manual Stars! sample.

If the template contains no Type 46, v6.8.1 still does not invent one.

FileHash is rebuilt after the complete order stream is known, so both submit
blocks are included in its byte count.

## Scout deconfliction

Reconnaissance now has two deconfliction layers.

### Planning-time reservation

An unknown planet is reserved if:
- a scout/recon order already targets it this turn, or
- another scout/recon fleet is already travelling to it in the current M file.

Other fleet roles do not reserve a world from reconnaissance merely because
they have that destination.

### Final safety barrier

After all strategy modules run, all new Scan/Recon movement orders are checked
again.

If two recon orders target the same planet:
1. the higher-priority order keeps it;
2. the duplicate scout is retargeted to the nearest fuel-safe unreserved unknown;
3. if no alternative exists, the duplicate movement is skipped/held.

This prevents future independent strategy modules from accidentally sending
multiple scouts to the same unknown planet.

## Playtest recommendation

Run one host generation first.

Check Player 2's decision JSON for:

```text
x_lifecycle:
  fresh_salt: ...
  template_salt: ...
  m_salt: ...
  save_submit_count: 2
```

For exploration, inspect fleet destinations and verify every new `scan` mission
has a unique destination planet.
