"""Stable paths and safe copies for immutable test-resource inputs."""

from __future__ import annotations

import shutil
from pathlib import Path


TEST_RESOURCE_ROOT = Path(__file__).resolve().parents[1] / "TestResource"


def test_resource(*parts: str) -> Path:
    """Return an immutable fixture path independent of the current directory."""

    target = TEST_RESOURCE_ROOT.joinpath(*parts)
    try:
        target.resolve().relative_to(TEST_RESOURCE_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("Test resource path must remain under TestResource") from exc
    return target


def copy_test_resource(source: Path, destination: Path) -> Path:
    """Copy a fixture to a caller-owned temporary work location."""

    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return destination
