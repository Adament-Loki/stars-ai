# v7.5 Midgame Play-On Mode

Autoplay has two explicit startup modes:

- `"play_on": false` (default) validates the immutable seed and restores it
  beside `stars_exe` before playing.
- `"play_on": true` validates and continues the current game beside
  `stars_exe` without staging or resetting from the seed.

`turns` always means the number of turns to play in this invocation. For
example, if the sandbox is currently at 2430, this configuration plays through
2440:

```json
{
  "play_on": true,
  "turns": 10
}
```

## Play-on safety barrier

Before output cleanup or any live-game mutation, play-on mode requires:

- exactly one live HST and XY for the seed game basename;
- one live M-file for every configured player;
- matching seed/live game ids;
- correct M-file player seats and native file types;
- one common current turn across the live HST and every configured M-file.

Failure stops before the sandbox is changed. The run’s `bootstrap` directory is
a byte-for-byte snapshot of the current midgame files and its manifest records
`"mode": "play_on"` and the actual starting turn.

As of v7.7, `"auto_merge_history": true` merges every configured player's
current `.m#` into `.h#` at play-on startup. `"require_history_sync": true`
then verifies embedded observation turns before any new `.x#` is generated.
No per-player client opening is required.

Stars! normally consumes live X files after hosting, so play-on does not require
them. It preserves a matching persistent template when available or recreates a
missing template from the seed’s validated X file. The seed supplies only the
known-good X authentication/static structure; it never replaces midgame HST,
XY, M, or history files in play-on mode.

Persistent AI memory is also retained. If it belongs to a different game or a
later year, the existing memory safeguards reset it rather than applying stale
knowledge to the resumed game.
