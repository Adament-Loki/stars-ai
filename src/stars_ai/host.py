from __future__ import annotations
import json
from pathlib import Path
from .adapters.json_adapter import JsonTurnAdapter
from .agent import StarsAgent
from .memory import AgentMemory


def run_manifest(manifest_path: str | Path) -> list[Path]:
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base = manifest_path.parent
    adapter = JsonTurnAdapter()
    outputs = []

    for player in manifest["ai_players"]:
        player_id = int(player["player_id"])
        state_path = (base / player["state"]).resolve()
        out_path = (base / player["orders"]).resolve()
        memory_path = (base / player["memory"]).resolve()

        state = adapter.read_state(state_path, player_id)
        memory = AgentMemory.load(memory_path)
        orders = StarsAgent(state, memory).play_turn()
        adapter.write_orders(orders, out_path)
        memory.save(memory_path)
        outputs.append(out_path)

    return outputs
