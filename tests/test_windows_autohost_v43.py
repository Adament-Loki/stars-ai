
from pathlib import Path
from stars_ai.windows_autohost import WindowsAutoHostConfig, _host_command

def test_exact_stars_host_command():
    cfg=WindowsAutoHostConfig(
        stars_exe=r"C:\Stars\stars!.exe",
        seed_dir="seed", output_dir="out", game_name="AIPLAY"
    )
    cmd=_host_command(cfg,Path(r"C:\tmp\AIPLAY.hst"))
    assert cmd[0].endswith("stars!.exe")
    assert cmd[1]=="-g"
    assert cmd[-1].endswith("AIPLAY.hst")

def test_password_precedes_hst():
    cfg=WindowsAutoHostConfig(
        stars_exe="stars!.exe", seed_dir="seed", output_dir="out",
        game_name="AIPLAY", host_password="secret"
    )
    cmd=_host_command(cfg,Path("AIPLAY.hst"))
    assert cmd==["stars!.exe","-g","-p","secret","AIPLAY.hst"]
