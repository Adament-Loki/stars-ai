
from pathlib import Path
from stars_ai.windows_autohost import WindowsAutoHostConfig

def test_direct_seed_defaults():
    cfg=WindowsAutoHostConfig(
        stars_exe="stars!.exe",
        seed_dir="seed",
        output_dir="logs",
        game_name="g",
    )
    assert cfg.use_seed_as_live is False
    assert cfg.play_on is False
    assert cfg.auto_merge_history is True
    assert cfg.require_history_sync is True
    assert cfg.host_timeout_seconds == 180
    assert cfg.pre_host_audit is True
    assert cfg.print_observer_each_turn is True

def test_source_stages_seed_into_executable_directory():
    import stars_ai.windows_autohost as w
    src=Path(w.__file__).read_text(encoding="utf-8")
    assert "game=_stars_execution_dir(cfg)" in src
    assert "_stage_seed_game(validated,game)" in src
    assert "READY TO HOST" in src
    assert "_print_observer_summary" in src
