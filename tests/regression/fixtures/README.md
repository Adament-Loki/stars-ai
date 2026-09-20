# Refactor behavior fixtures

`expansion_turn` is the fixed normalized turn captured by the behavioral
contract test. Its source state is the committed JSON example and its reviewed
semantic output is `semantic_contract.json`.

`native_transaction` is a minimal decrypted native order stream. It validates
the canonical transaction fingerprint independently of the variable X-file
header salt and FileHash record.

The focused production fixtures already maintained by the test suite are the
representative baseline corpus for the remaining behavioral areas:

| Area | Representative fixture/test |
| --- | --- |
| Exploration | `tests/test_persistent_exploration_campaign_v70.py` |
| Colonization | `tests/test_colony_logistics_v56.py` |
| Economy/production | `tests/test_resource_management_v90.py` |
| Research | `tests/test_research_capabilities_v81.py` |
| Transport | `tests/test_shared_transport_v103.py` |
| Military/defense | `tests/test_territorial_minefield_doctrine_v109.py` |
| Ship design | `tests/test_ship_design_golden_v111.py` |
| Persistent memory | `tests/test_persistent_intel_v70.py` |
| Waypoint continuation | `tests/test_multi_waypoint_routes_v73.py` |
| Native writer safety | `tests/test_safe_native_writer_v491.py` |
