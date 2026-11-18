"""Optional strategy director. It chooses menu ids. It never writes orders.

A provider returns text. This module checks that text against a strict shape.
A bad id falls back to the rule engine for that decision. A broken response,
timeout, or provider error falls back for every decision and records why.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol


class DirectorError(Exception):
    """The provider did not return a usable response."""


class DirectorProvider(Protocol):
    def complete(self, prompt: str) -> str:
        """Return the model text for one digest and menu."""


@dataclass
class StubDirectorProvider:
    """Test double. ``error`` is raised instead of returning ``response``."""

    response: str = ""
    error: Exception | None = None

    def complete(self, prompt: str) -> str:
        if self.error is not None:
            raise self.error
        return self.response


@dataclass
class OpenAICompatibleProvider:
    """Chat-completions client. The key stays in the environment."""

    model: str
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "LLM_DIRECTOR_API_KEY"
    timeout_seconds: float = 20.0

    def complete(self, prompt: str) -> str:
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise DirectorError(f"{self.api_key_env} is not set")
        body = json.dumps({
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": "Reply with one JSON object and no other text."},
                {"role": "user", "content": prompt},
            ],
        }).encode("utf-8")
        request = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise DirectorError(str(exc)) from exc
        try:
            return str(payload["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise DirectorError("provider response has no message content") from exc


@dataclass
class DirectorResult:
    strategic_posture: str
    choices: list[dict[str, str]]
    notes_for_next_turn: str
    fallbacks: list[dict[str, str]] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    prompt: str = ""
    raw_response: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategic_posture": self.strategic_posture,
            "choices": self.choices,
            "notes_for_next_turn": self.notes_for_next_turn,
            "fallbacks": self.fallbacks,
            "log": self.log,
        }


class LLMDirector:
    def __init__(self, provider: DirectorProvider):
        self.provider = provider

    def choose(self, digest: dict[str, Any], menu: dict[str, Any]) -> DirectorResult:
        by_id = {row["option_id"]: row for row in menu.get("options") or []}
        engine = _engine_by_category(menu)
        prompt = _prompt(digest, menu)
        try:
            raw = self.provider.complete(prompt)
        except Exception as exc:
            return _fallback_all(engine, by_id, f"{type(exc).__name__}: {exc}", prompt, "")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            return _fallback_all(engine, by_id, f"malformed JSON: {exc}", prompt, raw)
        if not isinstance(parsed, dict):
            return _fallback_all(engine, by_id, "response was not a JSON object", prompt, raw)
        posture = parsed.get("strategic_posture")
        notes = parsed.get("notes_for_next_turn")
        rows = parsed.get("choices")
        if not isinstance(posture, str) or not isinstance(notes, str) or not isinstance(rows, list):
            return _fallback_all(engine, by_id, "response is missing posture, choices, or notes", prompt, raw)
        chosen: dict[str, dict[str, str]] = {}
        log: list[str] = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("option_id"), str) or not isinstance(row.get("reason"), str):
                log.append("dropped a choice that was not an option_id and reason")
                continue
            option = by_id.get(row["option_id"])
            if option is None:
                log.append(f"invalid id {row['option_id']}; that decision stays with the rule engine")
                continue
            chosen[str(option["category"])] = {"option_id": row["option_id"], "reason": row["reason"]}
        choices = []
        fallbacks = []
        for category, engine_id in engine.items():
            pick = chosen.get(category)
            if pick is None:
                fallbacks.append({
                    "option_id": engine_id,
                    "reason": next((line for line in log if category in line or "invalid" in line), "director did not choose this decision"),
                })
                choices.append({"option_id": engine_id, "reason": "rule engine"})
            else:
                choices.append(pick)
        return DirectorResult(
            strategic_posture=posture,
            choices=choices,
            notes_for_next_turn=notes,
            fallbacks=fallbacks,
            log=log,
            prompt=prompt,
            raw_response=raw,
        )


def _engine_by_category(menu: dict[str, Any]) -> dict[str, str]:
    options = {row["option_id"]: row for row in menu.get("options") or []}
    chosen: dict[str, str] = {}
    for option_id in menu.get("engine_choices") or []:
        row = options.get(option_id)
        if row is not None:
            chosen[str(row["category"])] = option_id
    return chosen


def _fallback_all(engine: dict[str, str], by_id: dict[str, dict], reason: str, prompt: str, raw: str) -> DirectorResult:
    return DirectorResult(
        strategic_posture="rule_engine",
        choices=[{"option_id": option_id, "reason": "rule engine"} for option_id in engine.values()],
        notes_for_next_turn="",
        fallbacks=[{"option_id": option_id, "reason": reason} for option_id in engine.values()],
        log=[reason],
        prompt=prompt,
        raw_response=raw,
    )


def _prompt(digest: dict[str, Any], menu: dict[str, Any]) -> str:
    lines = [
        "Choose one listed option_id per decision. Do not invent ids.",
        json.dumps({"digest": digest, "menu": menu}, default=str),
    ]
    return "\n".join(lines)
