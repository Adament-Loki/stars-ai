
# v6.0 Full Research Mapping

ResearchChange native payloads:

- Energy          -> `0F 60`
- Weapons         -> `0F 61`
- Propulsion      -> `0F 62`
- Construction    -> `0F 63`
- Electronics     -> `0F 64`
- Biotechnology   -> `0F 65`

Empirically confirmed:
- Propulsion
- Electronics
- Biotechnology

High-confidence contiguous extrapolation:
- Energy
- Weapons
- Construction

The strategy-selected research field is now emitted directly. The previous
Electronics fallback has been removed.

The writer still only emits the observed normal 100% field-switch form.
