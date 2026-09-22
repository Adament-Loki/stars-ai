# Runtime X-template policy

The native host creates known-good `.x#` templates from a validated seed and
keeps the resulting mutable template directory beside that runtime seed. The
runtime does not ship game-specific X templates because their headers, player
identity, and encryption material are game-specific.

Captured scenario templates belong under `tests/TestResource/native/x-templates/`
and must be copied to a temporary runtime location before a test mutates them.
