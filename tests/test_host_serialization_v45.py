
import json
import subprocess

import pytest

from stars_ai.windows_autohost import (
    WindowsAutoHostConfig,
    _file_signature,
    _run_host_serialized,
)

def test_host_serialization_defaults(tmp_path):
    cfg=WindowsAutoHostConfig(stars_exe="stars!.exe",seed_dir="s",output_dir="o",game_name="g")
    assert cfg.prevent_parallel_stars is True
    assert cfg.host_settle_seconds > 0
    assert cfg.host_timeout_seconds == 180

def test_file_signature_changes(tmp_path):
    p=tmp_path/"a"
    p.write_bytes(b"a")
    a=_file_signature([p])
    p.write_bytes(b"abcd")
    b=_file_signature([p])
    assert a != b


def test_host_timeout_writes_modal_vs_slow_diagnostic(tmp_path,monkeypatch):
    hst=tmp_path/"GAME.hst"
    hst.write_bytes(b"host")
    cfg=WindowsAutoHostConfig(
        stars_exe=str(tmp_path/"stars!.exe"),
        seed_dir="s",
        output_dir="o",
        game_name="g",
        prevent_parallel_stars=False,
        host_timeout_seconds=1,
    )
    def timeout(*args,**kwargs):
        raise subprocess.TimeoutExpired(args[0] if args else "stars",1)
    monkeypatch.setattr(subprocess,"run",timeout)
    with pytest.raises(RuntimeError,match="host timed out"):
        _run_host_serialized(cfg,hst,[hst],tmp_path)
    diagnostic=json.loads((tmp_path/"current-HOST-TIMEOUT.json").read_text())
    assert diagnostic["timeout_seconds"]==1
    assert "assessment" in diagnostic
    assert diagnostic["tracked_files_changed"] is False
