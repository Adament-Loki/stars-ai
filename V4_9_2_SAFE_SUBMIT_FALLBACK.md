
# v4.9.2 Safe Submit Fallback

Some valid initial seed `.x#` templates do not contain a type-46
`SaveAndSubmit` block. v4.9.1 incorrectly treated that as fatal.

v4.9.2 behavior:

1. If the template contains type 46, preserve it byte-for-byte.
2. If it does not, emit the exact type-46 payload observed in two independent
   Stars!-generated research submissions:

```
type = 46
length = 4
payload = 01 01 05 19
```

The old zero-length type-46 form remains prohibited.

All other v4.9.1 safe-mode restrictions remain:
- native Colonize task disabled until captured from Stars!
- only directly observed Electronics/Biotechnology research encodings enabled
- normal fleet/scout movement and validated production remain enabled

Validation: start from a fresh seed and run one turn first.
