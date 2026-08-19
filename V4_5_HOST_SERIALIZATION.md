
# v4.5 Host Serialization Fix

v4.5 fixes a Windows/legacy-Stars! race where the executable can return control
before the underlying host-generation activity has completely finished.

The autoplay controller now enforces:

1. No Stars! process may already be running before host generation begins.
2. Only one host invocation is launched.
3. The controller waits for the configured Stars! process to exit when detectable.
4. It waits for `.hst`, `.xy`, and `.m1`–`.m4` output files to change.
5. It then requires those files to remain stable for a configurable settle period.
6. Only after that does the next AI turn begin.

Recommended first test:

```json
"turns": 2,
"host_poll_seconds": 0.5,
"host_settle_seconds": 1.5,
"prevent_parallel_stars": true
```

Before running, close every manually opened Stars! window.

Then:

```powershell
Get-Process | Where-Object { $_.ProcessName -like "*stars*" }
```

If no Stars! process is running:

```powershell
.\run-autoplay.ps1 -Config .\autoplay-config.json
```
