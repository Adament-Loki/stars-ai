
from pathlib import Path
from stars_ai.windows_autohost import WindowsAutoHostConfig, _file_signature

def test_host_serialization_defaults(tmp_path):
    cfg=WindowsAutoHostConfig(stars_exe="stars!.exe",seed_dir="s",output_dir="o",game_name="g")
    assert cfg.prevent_parallel_stars is True
    assert cfg.host_settle_seconds > 0

def test_file_signature_changes(tmp_path):
    p=tmp_path/"a"
    p.write_bytes(b"a")
    a=_file_signature([p])
    p.write_bytes(b"abcd")
    b=_file_signature([p])
    assert a != b
