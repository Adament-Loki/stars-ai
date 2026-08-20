# v7.2 Safe Staging and Active Waypoints

Autoplay now treats `seed_dir` as an immutable, complete starting game. It
discovers the single game basename, validates `.hst`, `.xy`, and every configured
player's `.m#`/initial `.x#`, and fails before touching the live Stars! directory
if any registration, turn, structure, or FileHash check fails.

After validation, only files for that basename are cleared beside `stars_exe`.
The complete seed game is copied there, a bootstrap snapshot/manifest is written
under `output_dir`, and every later native operation uses the executable directory.

Native waypoint #1 is now authoritative. Planet target encodings with preserved
upper target bits are normalized into destination, warp, task, and safely known
mission fields. Identical mission requests are `CONTINUE` operations that emit no
replacement movement block. Different destinations are `BLOCKED RETARGET`; the
synthetic Type-5 movement replacement is disabled pending a controlled native
sample. Existing validated Add-then-task sequences remain enabled for a fleet that
does not yet have waypoint #1.

Decision traces include native/requested waypoint state and `ADD`, `CONTINUE`, or
blocked outcomes. Persistent diagnostics compare year-to-year range using the
approximate `warp²` movement baseline and flag repeated severe underperformance.
The host timeout default is 180 seconds with modal/slow-host diagnostic output.
