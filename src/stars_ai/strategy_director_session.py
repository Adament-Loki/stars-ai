"""Run the optional director around one turn without letting it write files.

``off`` does nothing. ``advise`` records what the director would pick beside
the rule engine. ``decide`` retargets orders the engine already emitted.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stars_ai.llm_director import DirectorProvider, DirectorResult, LLMDirector, OpenAICompatibleProvider
from stars_ai.strategy_digest import build_strategy_digest
from stars_ai.strategy_guardrails import budget_block, rejection
from stars_ai.strategy_menu import build_option_menu

MODES = {"off", "advise", "decide"}


def consult_strategy_director(state: Any, orders: Any) -> dict[str, Any] | None:
    """Honor ``state.native['llm_director_mode']``. Missing or off is a no-op."""
    native = getattr(state, "native", None) or {}
    mode = str(native.get("llm_director_mode") or "off").lower()
    if mode not in MODES or mode == "off":
        return None
    notes_path = native.get("llm_director_notes_path")
    prior = _load_notes(notes_path)
    digest = build_strategy_digest(state)
    if prior:
        digest["notes_from_prior_turn"] = prior
    menu = build_option_menu(state)
    blocked = budget_block(state, digest, menu)
    if blocked:
        result = DirectorResult(
            strategic_posture="rule_engine",
            choices=[{"option_id": option_id, "reason": "rule engine"} for option_id in menu.get("engine_choices") or []],
            notes_for_next_turn=prior,
            fallbacks=[{"option_id": option_id, "reason": blocked} for option_id in menu.get("engine_choices") or []],
            log=[blocked],
        )
        record = _record(mode, result, menu, [])
        _write_log(native.get("llm_director_log_dir"), state, record)
        return record
    provider = native.get("llm_director_provider")
    if provider is None:
        provider = OpenAICompatibleProvider(
            model=str(native.get("llm_director_model") or "gpt-4.1-mini"),
            base_url=str(native.get("llm_director_base_url") or "https://api.openai.com/v1"),
            timeout_seconds=float(native.get("llm_director_max_seconds", 20)),
        )
    result = LLMDirector(provider).choose(digest, menu)
    if mode == "decide":
        applied = _apply(state, orders, menu, result)
    else:
        applied = []
    record = _record(mode, result, menu, applied)
    _write_log(native.get("llm_director_log_dir"), state, record)
    if notes_path and result.notes_for_next_turn:
        _save_notes(notes_path, result.notes_for_next_turn)
    return record


def _record(mode: str, result: Any, menu: dict[str, Any], applied: list[str]) -> dict[str, Any]:
    return {
        "mode": mode,
        "prompt": result.prompt,
        "raw_response": result.raw_response,
        "decision": result.to_dict(),
        "engine_choices": list(menu.get("engine_choices") or []),
        "applied": applied,
    }


def _apply(state: Any, orders: Any, menu: dict[str, Any], result: Any) -> list[str]:
    by_id = {row["option_id"]: row for row in menu.get("options") or []}
    applied = []
    for choice in result.choices:
        option = by_id.get(choice["option_id"])
        if option is None:
            continue
        reason = rejection(state, option)
        if reason:
            result.log.append(f"{choice['option_id']}: {reason}")
            result.fallbacks.append({"option_id": choice["option_id"], "reason": reason})
            continue
        payload = option.get("payload") or {}
        category = option["category"]
        if category == "research" and _retarget(orders, "set_research", lambda order: _set_research(order, payload)):
            applied.append(choice["option_id"])
        elif category == "colonize" and _retarget(orders, "colony_operation", lambda order: _set_planet(order, payload)):
            applied.append(choice["option_id"])
        elif category == "build_queue" and _retarget(orders, "set_planet_queue", lambda order: _set_queue(order, payload)):
            applied.append(choice["option_id"])
        elif category == "front":
            if payload.get("action") in {"hold", "retreat"}:
                removed = _drop_front_attacks(orders, int(payload["planet_id"]))
                if removed:
                    applied.append(choice["option_id"])
            else:
                applied.append(choice["option_id"])
    return applied


def _retarget(orders: Any, kind: str, edit) -> bool:
    found = False
    for order in orders.orders:
        if order.kind == kind and edit(order):
            found = True
    return found


def _set_research(order: Any, payload: dict[str, Any]) -> bool:
    order.payload["current_field"] = payload["current_field"]
    order.payload["next_field"] = payload["next_field"]
    order.payload["allocation_percent"] = payload.get("allocation_percent", order.payload.get("allocation_percent", 15))
    return True


def _set_planet(order: Any, payload: dict[str, Any]) -> bool:
    order.payload["destination_planet_id"] = int(payload["destination_planet_id"])
    return True


def _set_queue(order: Any, payload: dict[str, Any]) -> bool:
    if int(order.payload.get("planet_id", -1)) != int(payload["planet_id"]):
        return False
    order.payload["queue"] = list(payload["queue"])
    return True


def _drop_front_attacks(orders: Any, planet_id: int) -> bool:
    kept = []
    removed = False
    for order in orders.orders:
        destination = order.payload.get("destination_planet_id")
        mission = str(order.payload.get("mission") or "")
        if order.kind == "move_fleet" and destination == planet_id and mission in {"attack", "intercept", "bomb"}:
            removed = True
            continue
        kept.append(order)
    orders.orders[:] = kept
    return removed


def _load_notes(path: str | None) -> str:
    if not path:
        return ""
    file = Path(path)
    if not file.is_file():
        return ""
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    return str(payload.get("notes_for_next_turn") or "")


def _save_notes(path: str, notes: str) -> None:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps({"notes_for_next_turn": notes}, indent=2), encoding="utf-8")


def _write_log(directory: str | None, state: Any, record: dict[str, Any]) -> None:
    if not directory:
        return
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    turn = int((getattr(state, "native", {}) or {}).get("header", {}).get("turn", getattr(state, "year", 0)))
    player = int(getattr(state, "player_id", 0))
    (folder / f"turn-{turn}-player-{player}.json").write_text(
        json.dumps(record, indent=2, default=str),
        encoding="utf-8",
    )


def provider_for_tests(provider: DirectorProvider) -> dict[str, Any]:
    return {"llm_director_provider": provider}
