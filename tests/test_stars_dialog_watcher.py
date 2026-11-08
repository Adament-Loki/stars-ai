from stars_ai.stars_dialog_watcher import (
    command_mentions_executable,
    descendant_pids,
    select_dismiss_control,
)


def test_ok_is_preferred_over_cancel():
    assert select_dismiss_control([(2, "Cancel"), (1, "&OK")], 2) == 1


def test_yes_dismisses_a_confirmation():
    assert select_dismiss_control([(6, "&Yes"), (7, "&No")], 7) == 6


def test_default_button_is_used_when_its_caption_is_not_a_refusal():
    assert select_dismiss_control([(1, "Continue")], 1) == 1


def test_cancel_default_is_not_clicked():
    assert select_dismiss_control([(2, "Cancel")], 2) is None


def test_unknown_default_is_not_clicked_when_buttons_were_seen():
    assert select_dismiss_control([(2, "Cancel")], 1) is None


def test_launched_process_includes_its_wrapper_child():
    assert descendant_pids({10}, {10: 1, 20: 10, 30: 2}) == {10, 20}


def test_ntvdm_command_line_matches_only_the_launched_executable():
    command = (
        r'C:\otvdmw.exe --ntvdm64: "C:\Users\Jason\AppData\Local\Temp\stars-ai-50turn\live\starsjrc4.exe" '
        r"--ntvdm64-args: -g GAME.hst"
    )
    assert command_mentions_executable(
        command,
        r"C:\Users\Jason\AppData\Local\Temp\stars-ai-50turn\live\starsjrc4.exe",
    )
    assert not command_mentions_executable(command, r"C:\other\starsjrc4.exe")
