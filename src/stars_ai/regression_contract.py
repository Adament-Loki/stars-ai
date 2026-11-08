"""Capture stable behavioral evidence for refactor regression contracts.

This is an observability utility, not a planner or native-codec implementation.
It records the public effects of one fixed turn so a structural refactor can be
checked against the behavior that existed when the contract was frozen.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable

from .agent import StarsAgent
from .adapters.native_core_adapter import NativeCoreTurnAdapter
from .adapters.stars_native import NativeBlock, read_blocks
from .memory import AgentMemory
from .models import GameState
from .native.x_writer import ORDER_BLOCK_TYPES
from .turn_execution import write_ai_turn
from .persona import BalancedPersona, StrategicPersona


VOLATILE_NATIVE_BLOCK_TYPES = frozenset({0, 8, 9})
"""Terminator, header/salt, and FileHash are not order semantics."""


def normalized_contract_value(value: Any) -> Any:
    """Make a deterministic JSON-safe value without omitting behavior data."""
    if is_dataclass(value):
        return normalized_contract_value(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): normalized_contract_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [normalized_contract_value(item) for item in value]
    if isinstance(value, set):
        return sorted(normalized_contract_value(item) for item in value)
    return value


def persistent_state_changes(before: Any, after: Any) -> Any:
    """Return a compact, recursive before/after description of changed memory."""
    before = normalized_contract_value(before)
    after = normalized_contract_value(after)
    if before == after:
        return {}
    if isinstance(before, dict) and isinstance(after, dict):
        changed = {}
        for key in sorted(set(before) | set(after)):
            if key not in before:
                changed[key] = {"after": after[key]}
            elif key not in after:
                changed[key] = {"before": before[key]}
            else:
                delta = persistent_state_changes(before[key], after[key])
                if delta != {}:
                    changed[key] = delta
        return changed
    return {"before": before, "after": after}


def fingerprint_native_blocks(blocks: Iterable[NativeBlock]) -> str:
    """Hash decrypted order records while ignoring deliberately variable bytes.

    The fresh X encryption salt resides in the Type-8 header; Type-9 is the
    dependent FileHash length/checksum record.  Neither changes an AI order's
    meaning.  Every actual order block's type and decrypted body is retained,
    making order additions, removals, reordering, and payload changes visible.
    """
    semantic_blocks = [
        {"type_id": int(block.type_id), "data_hex": bytes(block.data).hex()}
        for block in blocks
        if int(block.type_id) in ORDER_BLOCK_TYPES
        and int(block.type_id) not in VOLATILE_NATIVE_BLOCK_TYPES
    ]
    encoded = json.dumps(
        semantic_blocks, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def fingerprint_native_transaction(path: str | Path) -> str:
    """Return a salt-independent fingerprint for a generated native X file."""
    _header, blocks, _raw = read_blocks(Path(path))
    return fingerprint_native_blocks(blocks)


def fingerprint_contract_capture(capture: dict[str, Any]) -> str:
    """Hash a complete normalized capture for compact checked-in baselines."""
    encoded = json.dumps(
        normalized_contract_value(capture), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def capture_semantic_turn(
    state: GameState,
    *,
    memory: AgentMemory | None = None,
    persona: StrategicPersona | None = None,
) -> dict[str, Any]:
    """Capture the deterministic planner contract for a normalized turn.

    The supplied state and memory are copied first, so callers can compare a
    refactor without mutating their fixture.  Normalized-mode has no native
    writer pass; emitted/skipped native lists are therefore intentionally
    empty rather than inferred.
    """
    state_copy = GameState.from_dict(deepcopy(state.to_dict()))
    memory_copy = deepcopy(memory) if memory is not None else AgentMemory()
    memory_before = asdict(memory_copy)
    agent = StarsAgent(
        state_copy,
        memory=memory_copy,
        persona=persona or BalancedPersona(),
    )
    orders = agent.play_turn()
    memory_after = asdict(memory_copy)
    return normalized_contract_value({
        "capture_kind": "semantic_turn",
        "normalized_input_state": state.to_dict(),
        "semantic_order_set": orders.to_dict(),
        "emitted_native_actions": [],
        "skipped_native_actions": [],
        "fleet_intents": agent.fleet_intents,
        "persistent_state_before": memory_before,
        "persistent_state_after": memory_after,
        "persistent_state_changes": persistent_state_changes(memory_before, memory_after),
        "waypoint_diagnostics": state_copy.native.get("waypoint_diagnostics", []),
    })


def capture_native_turn(
    *,
    player_id: int,
    m_path: str | Path,
    xy_path: str | Path,
    template_x_path: str | Path,
    output_x_path: str | Path,
    memory_path: str | Path | None = None,
    memory_output_path: str | Path | None = None,
    trace_path: str | Path | None = None,
    client_x_path: str | Path | None = None,
    persona_name: str = "Balanced",
    friend_player_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Capture both planner and writer evidence for one fixed native turn.

    This delegates serialization to the existing writer unchanged.  It is a
    harness for fixture runs, not a second native encoder.
    """
    m_path = Path(m_path)
    xy_path = Path(xy_path)
    output_x_path = Path(output_x_path)
    trace_path = Path(trace_path) if trace_path else output_x_path.with_suffix(".trace.json")
    memory_path = Path(memory_path) if memory_path else None
    memory_output_path = Path(memory_output_path) if memory_output_path else memory_path

    input_state = NativeCoreTurnAdapter(xy_path=xy_path).read_state(m_path, player_id)
    memory_before = asdict(AgentMemory.load(memory_path))
    temporary_memory = TemporaryDirectory() if memory_output_path is None else None
    if temporary_memory is not None:
        memory_output_path = Path(temporary_memory.name) / "captured-memory.json"
    try:
        result = write_ai_turn(
            player_id=player_id,
            m_path=m_path,
            xy_path=xy_path,
            template_x_path=Path(template_x_path),
            output_x_path=output_x_path,
            client_x_path=Path(client_x_path) if client_x_path else None,
            persona_name=persona_name,
            trace_path=trace_path,
            friend_player_ids=friend_player_ids,
            memory_path=memory_path,
            memory_output_path=memory_output_path,
        )
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        memory_after = asdict(AgentMemory.load(memory_output_path))
    finally:
        if temporary_memory is not None:
            temporary_memory.cleanup()
    return normalized_contract_value({
        "capture_kind": "native_turn",
        "normalized_input_state": input_state.to_dict(),
        "semantic_order_set": trace.get("orders", {}),
        "emitted_native_actions": result.emitted,
        "skipped_native_actions": result.skipped,
        "fleet_intents": trace.get("fleet_intents", []),
        "persistent_state_before": memory_before,
        "persistent_state_after": memory_after,
        "persistent_state_changes": persistent_state_changes(memory_before, memory_after),
        "waypoint_diagnostics": result.waypoint_diagnostics,
        "native_transaction_fingerprint": fingerprint_native_transaction(output_x_path),
    })


def write_contract_snapshot(capture: dict[str, Any], path: str | Path) -> None:
    """Write a reviewed baseline snapshot outside normal test execution."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(normalized_contract_value(capture), indent=2) + "\n",
        encoding="utf-8",
    )
