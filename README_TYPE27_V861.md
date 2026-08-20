# STARS! AI v8.6.1 — Type27 Fresh-Create Hotfix

This is a narrow overlay for v8.6. It does **not** disable native ship creation.
It corrects the free-slot fresh-design Type27 lifecycle using the newest
client-generated GAME.x2 evidence.

## What the Turn-3 logs proved

Player 1 entered turn 2402 with ship design slots 0..3 occupied and slot 4 free.
The AI generated an Onion Privateer into free slot 4. No delete was involved.
The generated Type27 pair was:

    11 A4  [empty staging body, embedded slot byte 10]
    11 A4  [populated final body, embedded slot byte 10]

The host never advanced and the user reported the X as corrupt.

## What the new client-generated Player-2 GAME.x2 shows

The file contains an unrelated temporary copy/delete sequence first:

    11 A4  [Mayflower (2), embedded slot byte 11]
    10 64  [delete that same-turn transient slot-4 copy]

That is a distinct client UI path. Do not use it as the AI fresh-create model.

The subsequent fresh-from-scratch Fuel-Mizer Colony Ship creation is:

    11 A4  [empty Colony Ship staging, embedded slot byte 10]
    11 64  [populated Colony Ship final, embedded slot byte 10]

This matches the older controlled Medium-Freighter evidence: fresh design creation
uses A0|slot for staging and 60|slot for final.

The P2 final design in this upload is a Fuel-Mizer Colony Ship, not an Onion
Privateer. It is still a strong control sample for the same free-slot Type27
fresh-create lifecycle.

## v8.6.1 behavior

* `create_ship_design` remains enabled.
* Free-slot create uses `11 A0|slot` staging -> `11 60|slot` final.
* Staging keeps the base-hull name; final may use the custom AI design name.
* Embedded design slot bit0 must remain clear for this fresh-create path.
* Atomic delete+create is still forbidden.
* Deleting an existing M-file design still requires zero live ships, zero queued
  builds, and zero remaining production.
* The `10 64` in the new P2 reference is a same-turn transient-copy delete and
  is **not** generalized to existing-M design deletion.

## Install over v8.6

Copy/extract this package over the repository root so that:

    src/stars_ai/native/design_change.py

replaces the v8.6 version. `x_writer.py` does not need another patch; it already
calls `create_ship_design_blocks()` from this module.

Run:

    pytest -q tests/test_ship_design_v83.py tests/test_type27_client_turn3_v861.py

Then generate the next X but do not host it until the verifier passes:

    python .\VERIFY_TYPE27_V861.py .\sandbox\GAME.x1

For an AI fresh create in slot 4, expect:

    STAGING CREATE slot 4 control=11 a4 ... embedded_slot_byte=0x10
    FINAL CREATE slot 4 control=11 64 ... embedded_slot_byte=0x10
    PASS: Type27 stream matches v8.6.1 fresh-create lifecycle invariants.

## Validation performed here

* Exact current user GAME.x2 fresh Colony Ship staging/final bytes reproduced by
  the encoder.
* Focused Type27/design tests: 8 passed.
* New verifier rejects the actual corrupt P1 Turn-3 A4/A4 stream.
* A structural copy of the same P1 X with only the final control changed A4->64
  passes the v8.6.1 verifier.

This is structural/client-delta evidence only. Host acceptance and next-M/original
client readback remain the required proof before promoting generic free-slot
creation from experimental to validated.
