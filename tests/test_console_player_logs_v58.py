
from stars_ai.windows_autohost import WindowsAutoHostConfig, IntegratedNativeOrderBridge

def _cfg(**kw):
    base=dict(stars_exe="stars.exe",seed_dir="seed",output_dir="out",game_name="GAME")
    base.update(kw)
    return WindowsAutoHostConfig(**base)

def test_console_player_logs_default_all():
    cfg=_cfg()
    assert cfg.console_player_logs is None
    b=IntegratedNativeOrderBridge(cfg.personas,cfg.console_player_logs)
    assert b.console_player_logs is None

def test_console_player_logs_selective():
    cfg=_cfg(console_player_logs=[1,4])
    b=IntegratedNativeOrderBridge(cfg.personas,cfg.console_player_logs)
    assert b.console_player_logs == {1,4}

def test_console_player_logs_empty_suppresses_all():
    cfg=_cfg(console_player_logs=[])
    b=IntegratedNativeOrderBridge(cfg.personas,cfg.console_player_logs)
    assert b.console_player_logs == set()
