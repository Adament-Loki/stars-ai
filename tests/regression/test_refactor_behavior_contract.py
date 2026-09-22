"""Frozen behavior checks used before and during source-layout refactors."""

from __future__ import annotations

import json

from stars_ai.adapters.json_adapter import JsonTurnAdapter
from stars_ai.adapters.stars_native import NativeBlock
from stars_ai.regression_contract import (
    capture_semantic_turn,
    fingerprint_contract_capture,
    fingerprint_native_blocks,
)


from tests.helpers.resources import test_resource as resource_path


FIXTURES = resource_path("regression")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_expansion_turn_semantic_contract_is_frozen():
    fixture_dir = FIXTURES / "expansion_turn"
    fixture = _load_json(fixture_dir / "fixture.json")
    state_path = (fixture_dir / fixture["state_source"]).resolve()
    state = JsonTurnAdapter().read_state(state_path, player_id=2)

    actual = capture_semantic_turn(state)
    expected = _load_json(fixture_dir / "semantic_contract.json")

    assert fingerprint_contract_capture(actual) == expected["capture_sha256"]
    assert len(actual["semantic_order_set"]["orders"]) == expected["semantic_order_count"]
    assert len(actual["fleet_intents"]) == expected["fleet_intent_count"]
    assert actual["normalized_input_state"]["year"] == 2405
    assert actual["fleet_intents"]
    assert actual["persistent_state_changes"]


def test_native_transaction_fingerprint_ignores_salt_and_filehash_but_not_orders():
    fixture = _load_json(FIXTURES / "native_transaction" / "transaction.json")
    blocks = [
        NativeBlock(int(block["type_id"]), len(bytes.fromhex(block["data_hex"])), bytes.fromhex(block["data_hex"]))
        for block in fixture["blocks"]
    ]
    fingerprint = fingerprint_native_blocks(blocks)

    changed_header = list(blocks)
    changed_header[0] = NativeBlock(8, 16, bytes.fromhex("ffeeddccbbaa99887766554433221100"))
    changed_filehash = list(changed_header)
    changed_filehash[-2] = NativeBlock(9, 17, bytes.fromhex("11" * 17))
    changed_order = list(changed_filehash)
    changed_order[1] = NativeBlock(4, 6, bytes.fromhex("020035127856"))

    assert fingerprint == fixture["expected_fingerprint"]
    assert fingerprint_native_blocks(changed_filehash) == fingerprint
    assert fingerprint_native_blocks(changed_order) != fingerprint
