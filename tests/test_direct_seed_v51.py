
from pathlib import Path
from stars_ai.windows_autohost import WindowsAutoHostConfig

def test_direct_seed_defaults():
    cfg=WindowsAutoHostConfig(
        stars_exe="stars!.exe",
        seed_dir="seed",
        output_dir="logs",
        game_name="g",
    )
    assert cfg.use_seed_as_live is True
    assert cfg.pre_host_audit is True
    assert cfg.print_observer_each_turn is True

def test_source_uses_seed_as_game_in_direct_mode():
    import stars_ai.windows_autohost as w
    src=Path(w.__file__).read_text(encoding="utf-8")
    assert "game = seed if cfg.use_seed_as_live" in src
    assert "READY TO HOST" in src
    assert "_print_observer_summary" in src
