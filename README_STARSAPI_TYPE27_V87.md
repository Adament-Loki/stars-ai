# Stars! AI v8.7 — StarsAPI DesignBlock Type27 Diagnostic Build

## Purpose

v8.7 keeps autonomous ship creation enabled, but removes one major source of uncertainty from native ship design writing.

The embedded ship-design body is now produced by a direct Python port of StarsAPI's `DesignBlock.encode()` and checked by a matching port of `DesignBlock.decode()`.  Type27-specific code is limited to the two-byte `DesignChange` wrapper and lifecycle sequencing, because StarsAPI itself does **not** implement `DesignChangeBlock.encode()`.

Source reference used for the port:

- `stars-4x/starsapi` commit `194732ce0d018b8ca818e5d732d0b14c01739768`
- `DesignBlock.java` — body `encode()` / `decode()`
- `DesignChangeBlock.java` — strips two Type27 wrapper bytes and restores embedded DesignBlock byte-1 bit 0 before decoding; its own `encode()` is explicitly unimplemented.

This distinction is important:

```text
KNOWN / PORTED FROM STARSAPI
    DesignBlock first/second byte flags
    design slot bits
    hull id
    picture id
    armor
    slot count
    turn designed
    total built / remaining
    4-byte component slot tuples
    design name placement
    strict body-size validation
    Type27 embedded bit-0 normalization behavior

STILL EMPIRICAL
    first two Type27 wrapper bytes
    create/copy/edit/delete transaction sequencing
```

## What changed

### 1. New StarsAPI body codec

`src/stars_ai/native/starsapi_design_codec.py`

A normal full ship body is encoded exactly as StarsAPI does:

```text
byte 0      07
byte 1      01 | design_number<<2 | transfer/starbase flags
byte 2      hull id
byte 3      picture id
bytes 4-5   armor (LE u16)
byte 6      slot count
bytes 7-8   turn designed (LE u16)
bytes 9-12  total built (LE u32)
bytes 13-16 total remaining (LE u32)
then        category u16 + item byte + count byte, once per slot
then        Stars! encoded name bytes
```

For a Type27 fresh-create body, v8.7 first builds that ordinary StarsAPI body, then clears **only** byte-1 bit 0.  That mirrors the normalization performed by StarsAPI `DesignChangeBlock.decode()` before it calls `DesignBlock.decode()`.

Every generated body must:

1. decode under the StarsAPI rules;
2. restore the Type27 bit;
3. re-encode;
4. match the original bytes exactly.

If any of those fail, the body never reaches the X order stream.

### 2. `design_change.py` no longer lays out hull bodies itself

`src/stars_ai/native/design_change.py` now owns only:

- free-slot create lifecycle;
- dead-slot delete lifecycle;
- slot safety checks;
- Type27 wrapper parsing;
- payload conversion.

The actual embedded ship body comes from the StarsAPI codec.

### 3. Temporary Type27 isolation mode

The biggest diagnostic change is in `x_writer.py`, installed with:

```powershell
python .\APPLY_V87_STARSAPI_NATIVE_PATCH.py
```

When the planner has a **safe executable** `create_ship_design` or `delete_ship_design`, that player gets a clean Type27 diagnostic turn:

```text
strategy still computes all actions
        ↓
native writer preflights design-slot safety
        ↓
if safe Type27 mutation exists:
    emit ONLY that design mutation
    skip movement / production / research / cargo for that player this turn
    write one trailing client-style Type46 when the template supplies Type46
```

This is intentionally temporary. It costs one turn of normal execution, but gives a clean host experiment.

If the isolated X corrupts:

```text
remaining bug is Type27 wrapper/lifecycle itself
```

If the isolated X hosts successfully:

```text
DesignBlock + wrapper works in isolation
remaining bug is interaction/order-stream composition
```

### 4. Exact Type27 hex is written to the decision JSON

An emitted create/delete now includes:

```json
"type27_hex": ["...", "..."],
"design_body_codec": "StarsAPI DesignBlock.encode/decode port"
```

The trace also records:

```json
"type27_isolation": true,
"type27_isolation_kind": "create_ship_design",
"type27_isolation_slot": 4
```

So another failure can be diagnosed directly from the logs.

## Controlled sample results

v8.7's StarsAPI inspector was run against all ship-design samples currently supplied in this project:

```text
4 X files inspected
0 body failures
```

This includes the latest attached `GAME.x2`:

```text
#1 A4 — Mayflower (2) copy body
   embedded bit0 set
   ordinary StarsAPI DesignBlock round-trip: PASS

#2 10 64 — delete-like record

#3 A4 — fresh Colony Ship staging
   embedded bit0 clear
   StarsAPI DesignChange-normalized round-trip: PASS

#4 11 64 — fresh Colony Ship final
   embedded bit0 clear
   StarsAPI DesignChange-normalized round-trip: PASS
```

It also includes the older controlled Medium Freighter samples.  The clean `newship-medcargo-all components` sample is especially valuable because it contains exactly:

```text
11 A4  + StarsAPI-valid empty Medium Freighter body
11 64  + StarsAPI-valid populated Medium Freighter body
```

with no copy/delete preamble.  Therefore a copy/delete preamble is **not** required for every fresh design creation.

## Important finding about the failed Onion Privateer

The populated P1 Turn-3 Onion Privateer body from the failed X itself passes the StarsAPI codec:

```text
design slot: 4
hull: Privateer (11)
pic: 44
armor: 150
slot count: 5
turn designed: 2
engine: Fuel Mizer
shield slot: empty
3 x Fuel Tank
name: Onion Privateer
StarsAPI body decode/re-encode: PASS
```

That means v8.7 may produce the **same embedded design body bytes** as v8.6.1 for that design.  This release does not pretend otherwise.

The experiment is stronger because:

1. those bytes are now authoritative StarsAPI body bytes rather than our own layout;
2. malformed/trailing bytes fail closed;
3. the Type27 mutation is isolated from all other order families;
4. a single client-style trailing Type46 is used during isolation;
5. the exact Type27 bytes are logged.

If v8.7 still corrupts an isolated create, we should stop looking at the DesignBlock body and focus entirely on the two-byte Type27 wrapper / lifecycle semantics.

## Install

### If v8.6 / v8.6.1 is already installed

Extract this package over the repo root, then run:

```powershell
python .\APPLY_V87_STARSAPI_NATIVE_PATCH.py
pytest -q tests\test_starsapi_design_codec_v87.py tests\test_type27_client_turn3_v861.py tests\test_ship_design_v83.py
```

Do **not** rerun the baseline native patcher just to install v8.7.  The v8.7 patcher specifically upgrades an already-installed v8.6/v8.6.1 writer and refuses an unknown serializer.

### From clean public `main`

Public base remains commit:

```text
1c45444abef9982ab6af6bc94cb48c96783bcbaf
```

Run:

```powershell
python .\APPLY_NATIVE_WRITER_PATCH.py
python .\APPLY_V87_STARSAPI_NATIVE_PATCH.py
python .\APPLY_COLONY_LAYER1_PATCH.py
```

Then run the tests above or your normal suite.

## Before hosting the next design-creation turn

Run:

```powershell
python .\VERIFY_TYPE27_STARSAPI_V87.py .\path\to\GAME.x1
```

For a fresh slot-4 design, the expected diagnostic shape is:

```text
Type27 #1
    staging control = 11 a4
    embedded DesignBlock byte1 = 10 -> normalized 11
    StarsAPI body roundtrip = PASS

Type27 #2
    final control = 11 64
    embedded DesignBlock byte1 = 10 -> normalized 11
    StarsAPI body roundtrip = PASS

PASS
```

To inspect a client-created control file without applying the AI-only lifecycle rules:

```powershell
python .\INSPECT_TYPE27_STARSAPI_V87.py .\GAME.x2
```

## Delete/replacement safety is unchanged

Ship creation remains enabled.

Deletion remains allowed only when the current M state proves:

```text
existing design = yes
live ships using slot = 0
queued builds using slot = 0
total remaining = 0
```

There is still no same-turn atomic delete-and-recreate.  A dead design can be deleted on Turn N; the next M must show the slot free before creation on a later turn.
