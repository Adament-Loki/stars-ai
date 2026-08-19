
# v4.6 Persistent X Template Fix

## Symptom fixed

After one successful host generation:

```
Completed 1/50 host generations
RuntimeError: Integrated writer needs an initial known-good .x1 template ...
```

Stars! may consume/remove submitted `.x#` files when it generates the next turn.
v4.5 incorrectly expected the previous `.x#` to remain in the live directory.

## v4.6 behavior

At startup, the controller copies the initial seed `.x1`–`.x4` into:

```
<output_dir>\x-templates\
    template.x1
    template.x2
    template.x3
    template.x4
```

Those files are never submitted to Stars! and therefore remain available for
all 50 turns.

Each turn the integrated writer:

1. reads the current `.m#`
2. loads the persistent corresponding X template
3. updates the current game/turn header
4. writes current native orders
5. encrypts a fresh live `.x#`
6. submits that live `.x#` to Stars!

Whether Stars! deletes the submitted live `.x#` no longer matters.

## First validation run

Set:

```json
"turns": 3
```

Delete the old partially generated output run directory, then run:

```powershell
.\run-autoplay.ps1 -Config .\autoplay-config.json
```

After startup, verify:

```powershell
Get-ChildItem .\playtests\runs\fourAI\x-templates
```

You should see four persistent template files.
