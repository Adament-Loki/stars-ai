"""Access the ephemeral typed reasoning state attached to an AI planning turn.

``GameState.native`` is reserved for decoded Stars! data and adapter provenance.
During the incremental planner migration, legacy domain functions still accept a
``GameState`` rather than a ``TurnContext``.  ``StarsAI`` therefore attaches its
typed ``TurnAIState`` as a non-serialized private attribute for the lifetime of
the call.  These helpers give those functions a single compatibility boundary:
new facade turns use typed state; standalone legacy fixtures can continue to
provide the old native mapping until their callers are migrated.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def payload(state: Any, key: str) -> Any:
    """Return a detached current-turn payload, with legacy-fixture fallback."""

    turn = getattr(state, "_turn_ai_state", None)
    if turn is not None:
        value = turn.legacy_payload(key)
        if value is not None:
            return deepcopy(value)
    native = getattr(state, "native", None) or {}
    return deepcopy(native.get(key))


def mapping(state: Any, key: str) -> dict[str, Any]:
    """Return a mapping payload or the stable empty mapping."""

    value = payload(state, key)
    return dict(value) if isinstance(value, dict) else {}


def sequence(state: Any, key: str) -> list[Any]:
    """Return a sequence payload or the stable empty list."""

    value = payload(state, key)
    return list(value) if isinstance(value, list) else []


def publish(state: Any, key: str, value: Any) -> None:
    """Publish current-turn reasoning without expanding ``GameState.native``.

    Direct legacy calls without a ``StarsAI`` turn remain compatible: they keep
    writing their established scratch mapping because no typed turn exists to
    receive the value.  Facade turns always have the typed sink.
    """

    turn = getattr(state, "_turn_ai_state", None)
    if turn is not None and turn.set_legacy_payload(key, value):
        return
    native = getattr(state, "native", None)
    if isinstance(native, dict):
        native[key] = deepcopy(value)
