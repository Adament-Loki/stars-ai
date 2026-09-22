"""Phase 9 tests for portable, immutable test-resource inputs."""

from __future__ import annotations

import json

from tests.helpers.resources import (
    TEST_RESOURCE_ROOT,
    copy_test_resource,
    test_resource as resource_path,
)
from stars_ai.ai.state_store import AIStateStore
from stars_ai.autohost import load_windows_autohost_config
from stars_ai.memory import AgentMemory
from stars_ai.native.resources import native_resource_path


def test_resource_paths_are_stable_outside_the_repository_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert resource_path("sandboxes", "sandbox-native-reference", "GAME.m1").is_file()
    assert resource_path("native", "x-templates", "combat-cap-v117", "template.x1").is_file()
    assert resource_path("regression", "expansion_turn", "fixture.json").is_file()


def test_test_autoplay_config_is_resolved_relative_to_its_own_file(tmp_path, monkeypatch):
    config_path = resource_path("autoplay", "combat-cap-v117-autoplay.json")
    monkeypatch.chdir(tmp_path)

    config = load_windows_autohost_config(config_path)

    assert config.stars_exe == str(
        resource_path("sandboxes", "sandbox-combat-cap-v117", "starsjrc4.exe").resolve()
    )
    assert config.x_template_dir == str(
        resource_path("native", "x-templates", "combat-cap-v117").resolve()
    )
    assert config.ai_state_dir == str(resource_path("ai-state", "combat-cap-v117").resolve())
    assert "TestResource" not in config.output_dir


def test_ai_state_fixture_is_copied_before_mutation(tmp_path):
    fixture = resource_path("ai-state", "combat-cap-v117", "player-01-memory.json")
    original = fixture.read_bytes()
    working = copy_test_resource(fixture, tmp_path / fixture.name)

    memory = AgentMemory.load(working)
    memory.last_year = (memory.last_year or 0) + 1
    memory.save(working)

    assert working.read_bytes() != original
    assert fixture.read_bytes() == original
    assert AIStateStore(working).load().to_agent_memory().last_year == memory.last_year


def test_native_resource_path_is_package_relative_and_contains_runtime_mod_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert native_resource_path("mod", "data_hulls.mod").is_file()
    assert native_resource_path("mod", "data_unedited.mod").is_file()
    assert native_resource_path("x-templates", "README.md").is_file()


def test_test_autoplay_configs_are_plain_portable_json():
    config = json.loads(
        resource_path("autoplay", "defense-counter-v123-autoplay.json").read_text(encoding="utf-8")
    )

    assert not config["stars_exe"].startswith(("C:\\", "/"))
    assert not config["seed_dir"].startswith(("C:\\", "/"))


def test_every_test_autoplay_config_loads_without_a_cwd_assumption(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    for config_path in sorted(resource_path("autoplay").glob("*.json")):
        config = load_windows_autohost_config(config_path)
        assert config.stars_exe
        assert config.seed_dir
        assert config.output_dir


def test_captured_template_manifests_contain_no_machine_specific_paths():
    for manifest_path in sorted(resource_path("native", "x-templates").glob("*/templates.json")):
        payload = manifest_path.read_text(encoding="utf-8")
        assert "C:\\Dev\\repos\\stars-ai" not in payload
        assert json.loads(payload)["templates"]


def test_repository_root_has_no_obsolete_test_resource_directories():
    repository_root = TEST_RESOURCE_ROOT.parents[1]
    forbidden = (
        "sandbox",
        ".ai-state-combat-cap-v117",
        ".x-template-combat-cap-v117",
    )

    assert all(not (repository_root / name).exists() for name in forbidden)
    assert not list((repository_root / "src" / "stars_ai").rglob("*.bak"))
