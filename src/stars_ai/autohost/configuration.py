"""Configuration loading for the native autoplay application.

Autoplay configuration paths are interpreted relative to the JSON file that
declares them. This keeps checked-in examples and scenarios independent of
the caller's current working directory while preserving absolute-path
compatibility for existing local configurations.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import WindowsAutoHostConfig


_PATH_FIELDS = frozenset({
    "stars_exe",
    "seed_dir",
    "output_dir",
    "x_template_dir",
    "ai_state_dir",
})


def load_windows_autohost_config(path: str | Path) -> WindowsAutoHostConfig:
    """Load an autoplay configuration with paths anchored to its JSON file."""

    config_path = Path(path).expanduser().resolve()
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Autoplay configuration must be a JSON object: {config_path}")
    for field in _PATH_FIELDS:
        value = payload.get(field)
        if value in (None, ""):
            continue
        candidate = Path(str(value)).expanduser()
        if not candidate.is_absolute():
            payload[field] = str((config_path.parent / candidate).resolve())
    return WindowsAutoHostConfig(**payload)
