"""Stable lookup for packaged Stars! native reference resources."""

from __future__ import annotations

from pathlib import Path


NATIVE_RESOURCE_ROOT = Path(__file__).with_name("resources")
"""Native resources shipped with the Python package, never test fixtures."""


def native_resource_path(*parts: str) -> Path:
    """Return a package-relative native resource path without using the CWD."""

    return NATIVE_RESOURCE_ROOT.joinpath(*parts)
