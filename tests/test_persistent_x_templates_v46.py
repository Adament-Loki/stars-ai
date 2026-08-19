
from pathlib import Path
from stars_ai.windows_autohost import WindowsAutoHostConfig, _persistent_x_template_root


def test_default_persistent_template_location_is_outside_output_and_seed(tmp_path):
    seed=tmp_path/'game'; seed.mkdir()
    out=tmp_path/'output'
    cfg=WindowsAutoHostConfig(stars_exe='stars.exe',seed_dir=str(seed),output_dir=str(out),game_name='GAME')
    root=_persistent_x_template_root(cfg,seed.resolve())
    assert root.parent==seed.resolve().parent
    assert root!=seed.resolve()
    assert root!=out.resolve()


def test_explicit_template_dir_supported(tmp_path):
    seed=tmp_path/'game'; seed.mkdir()
    custom=tmp_path/'templates'
    cfg=WindowsAutoHostConfig(stars_exe='stars.exe',seed_dir=str(seed),output_dir=str(tmp_path/'out'),game_name='GAME',x_template_dir=str(custom))
    assert _persistent_x_template_root(cfg,seed.resolve())==custom.resolve()


def test_runner_uses_persistent_template_not_live_x_each_turn():
    src=Path(__file__).parents[1]/'src'/'stars_ai'/'windows_autohost.py'
    text=src.read_text(encoding='utf-8')
    assert '_bootstrap_persistent_x_templates' in text
    assert 'Never depend on a live/pre-existing GAME.x#' in text
    assert 'templates_root / f"template.x{player_id}"' in text
