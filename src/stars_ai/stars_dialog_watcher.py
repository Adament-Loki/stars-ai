"""Dismiss Stars! message boxes that block an unattended host turn.

Stars! sometimes stops turn generation on a modal dialog and waits for a
person to click it. This watcher looks only at visible dialogs owned by one
configured Stars! executable, then clicks a safe dismiss button such as OK or
Yes. It leaves Cancel, No, and any dialog that contains a text field alone, so
it does not confirm a file picker or submit a password.

The autoplay controller starts and stops a watcher around each host launch.
On current Windows the Stars! executable is started through ``otvdmw.exe``,
and the message box belongs to that wrapper rather than to ``stars.exe``.
The watcher follows the launched process, its children, and any process whose
command line names the configured executable. ``python -m
stars_ai.stars_dialog_watcher --exe <stars.exe>`` runs the same loop until
interrupted.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from pathlib import Path

DISMISS_LABELS = frozenset({"ok", "yes", "continue", "close"})
REFUSE_LABELS = frozenset({"no", "cancel", "abort"})
_DIALOG_CLASS = "#32770"


def normalize_button_label(text: str) -> str:
    """Drop the Windows accelerator marker and surrounding space."""

    return str(text or "").replace("&", "").strip().lower()


def select_dismiss_control(
    buttons: list[tuple[int, str]],
    default_id: int | None,
) -> int | None:
    """Choose a button id that dismisses a message box without refusing it.

    ``buttons`` is ``(control_id, caption)``. A caption of OK, Yes, Continue,
    or Close wins. The dialog's default button is used only when it is not
    No, Cancel, or Abort. A dialog whose only safe-looking control is missing
    returns None so the caller leaves it on screen.
    """

    labeled = [(int(control_id), normalize_button_label(caption)) for control_id, caption in buttons]
    for control_id, caption in labeled:
        if caption in DISMISS_LABELS:
            return control_id
    if default_id is None:
        return None
    default_id = int(default_id)
    for control_id, caption in labeled:
        if control_id == default_id and caption in REFUSE_LABELS:
            return None
    known = {control_id for control_id, _caption in labeled}
    if known and default_id not in known:
        return None
    return default_id


class StarsDialogWatcher:
    """Poll for Stars! dialogs owned by one executable and dismiss the safe ones.

    ``stars_exe`` is matched by full path when Windows can report it, so a
    second Stars! copy the user opened by hand is not clicked. Each dismissal
    is appended to ``log_path`` when that path is set, and kept on
    ``dismissed`` for the hosting turn's audit.
    """

    def __init__(
        self,
        stars_exe: str,
        *,
        log_path: Path | None = None,
        poll_seconds: float = 0.25,
    ):
        self.stars_exe = str(stars_exe)
        self.log_path = Path(log_path) if log_path else None
        self.poll_seconds = float(poll_seconds)
        self.dismissed: list[dict] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._roots: set[int] = set()
        self._roots_lock = threading.Lock()

    def watch_pid(self, pid: int) -> None:
        """Also accept dialogs owned by this process and its children.

        Windows may report the host as ``otvdmw.exe`` even though the
        controller launched ``stars.exe``. The process id comes from the
        launch itself, so the watcher does not have to guess from the image
        name.
        """

        with self._roots_lock:
            self._roots.add(int(pid))

    def start(self) -> None:
        """Start the background poll. On non-Windows this is a no-op."""

        if os.name != "nt" or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="stars-dialog-watcher",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> list[dict]:
        """Stop the poll and return every dialog this watcher dismissed."""

        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        return list(self.dismissed)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.dismiss_visible_dialogs()
            except Exception:
                # A failed window query must not kill the host turn.
                pass
            self._stop.wait(self.poll_seconds)

    def dismiss_visible_dialogs(self) -> list[dict]:
        """Click every current safe Stars! dialog. Returns the ones clicked."""

        if os.name != "nt":
            return []
        with self._roots_lock:
            roots = set(self._roots)
        clicked: list[dict] = []
        for hwnd in _visible_dialog_handles():
            if not _owned_by_host(hwnd, self.stars_exe, roots):
                continue
            if not _is_message_box(hwnd):
                continue
            if _contains_text_field(hwnd):
                continue
            action = _dismiss_dialog(hwnd)
            if action is None:
                continue
            self.dismissed.append(action)
            clicked.append(action)
            _append_log(self.log_path, action)
            print(
                f"[STARS DIALOG] Closed {action['title']!r} with {action['button']!r}.",
                flush=True,
            )
        return clicked


def main(argv: list[str] | None = None) -> None:
    """Run the watcher until Ctrl+C. Used while a host process is already up."""

    parser = argparse.ArgumentParser(
        description="Close Stars! message boxes owned by one executable.",
    )
    parser.add_argument("--exe", required=True, help="Stars! executable the host launched.")
    parser.add_argument("--log", help="Optional JSONL file of dismissed dialogs.")
    args = parser.parse_args(argv)
    watcher = StarsDialogWatcher(args.exe, log_path=Path(args.log) if args.log else None)
    watcher.start()
    if os.name != "nt":
        raise SystemExit("Stars! dialog dismissal is only available on Windows.")
    print(f"Watching {watcher.stars_exe} for Stars! message boxes.", flush=True)
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        watcher.stop()


def _append_log(path: Path | None, action: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(action) + "\n")


_WIN32 = None


def _win32():
    """Load user32 and kernel32 with pointer-sized window handles.

    The prototypes are set once. Without them, a 64-bit HWND passed through
    ctypes' default integer conversion can be truncated and the click goes
    nowhere.
    """

    global _WIN32
    if _WIN32 is not None:
        return _WIN32
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumChildWindows.argtypes = [wintypes.HWND, enum_proc, wintypes.LPARAM]
    user32.EnumChildWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetDlgCtrlID.argtypes = [wintypes.HWND]
    user32.GetDlgCtrlID.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = wintypes.LPARAM
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetDlgItem.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.Process32NextW.restype = wintypes.BOOL
    _WIN32 = (ctypes, wintypes, user32, kernel32, enum_proc)
    return _WIN32


def _visible_dialog_handles() -> list[int]:
    ctypes, wintypes, user32, _kernel32, enum_proc = _win32()
    handles: list[int] = []

    @enum_proc
    def collect(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            class_name = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, class_name, 64)
            if class_name.value == _DIALOG_CLASS:
                handles.append(int(hwnd))
        return True

    user32.EnumWindows(collect, 0)
    return handles


def _owned_by_host(hwnd: int, stars_exe: str, roots: set[int]) -> bool:
    ctypes, wintypes, user32, kernel32, _enum_proc = _win32()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process_id = int(pid.value)
    if process_id and process_id in descendant_pids(roots, _parent_map()):
        return True
    image = _process_image(process_id, ctypes, wintypes, kernel32)
    if image and _same_executable(image, stars_exe):
        return True
    command = _process_command_line(process_id)
    return bool(command and command_mentions_executable(command, stars_exe))


def descendant_pids(roots: set[int], parents: dict[int, int]) -> set[int]:
    """Return ``roots`` plus every process whose parent chain reaches one."""

    children: dict[int, list[int]] = {}
    for pid, parent in parents.items():
        children.setdefault(int(parent), []).append(int(pid))
    seen: set[int] = set()
    stack = [int(pid) for pid in roots if pid]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        stack.extend(children.get(pid, ()))
    return seen


def command_mentions_executable(command: str, stars_exe: str) -> bool:
    """True when a process command line names this exact Stars! executable.

    ``otvdmw.exe`` keeps the real executable path after ``--ntvdm64:``. An
    absolute path must appear in full so a different Stars! copy is left
    alone.
    """

    folded = str(command or "").casefold().replace("/", "\\")
    if not folded:
        return False
    candidate = str(Path(stars_exe)).casefold().replace("/", "\\")
    if candidate and candidate in folded:
        return True
    return (not Path(stars_exe).is_absolute()) and Path(stars_exe).name.casefold() in folded


def _same_executable(image: str, stars_exe: str) -> bool:
    left = Path(image)
    right = Path(stars_exe)
    try:
        if str(left.resolve()).casefold() == str(right.resolve()).casefold():
            return True
    except OSError:
        pass
    return left.name.casefold() == right.name.casefold() and not right.exists()


def _process_image(pid: int, ctypes, wintypes, kernel32) -> str | None:
    process_query = 0x1000
    handle = kernel32.OpenProcess(process_query, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def _parent_map() -> dict[int, int]:
    """Map each live process id to its parent. Empty when the snapshot fails."""

    ctypes, wintypes, _user32, kernel32, _enum_proc = _win32()
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if not snapshot or int(snapshot) == -1:
        return {}
    try:
        entry = _process_entry()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return {}
        parents: dict[int, int] = {}
        while True:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
        return parents
    finally:
        kernel32.CloseHandle(snapshot)


def _process_entry():
    ctypes, wintypes, _user32, _kernel32, _enum_proc = _win32()

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    return ProcessEntry()


def _process_command_line(pid: int) -> str | None:
    """Read a process command line. ``otvdmw.exe`` names Stars! there."""

    if not pid:
        return None
    ctypes, wintypes, _user32, kernel32, _enum_proc = _win32()
    ntdll = ctypes.WinDLL("ntdll")
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        needed = wintypes.ULONG(0)
        # ProcessCommandLineInformation (60) returns a UNICODE_STRING whose
        # characters follow the header in the same buffer.
        ntdll.NtQueryInformationProcess(handle, 60, None, 0, ctypes.byref(needed))
        if not needed.value or needed.value > 1_000_000:
            return None
        buffer = ctypes.create_string_buffer(needed.value)
        status = ntdll.NtQueryInformationProcess(
            handle, 60, buffer, needed.value, ctypes.byref(needed),
        )
        if status != 0 or needed.value < 8:
            return None
        length = int.from_bytes(buffer.raw[0:2], "little")
        text = buffer.raw[16:16 + length] if ctypes.sizeof(ctypes.c_void_p) == 8 else buffer.raw[8:8 + length]
        return text.decode("utf-16-le", errors="replace")
    finally:
        kernel32.CloseHandle(handle)


def _is_message_box(hwnd: int) -> bool:
    """True for a small fixed dialog, not the resizable Stars! game window."""

    ctypes, wintypes, user32, _kernel32, _enum_proc = _win32()
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    width = int(rect.right) - int(rect.left)
    height = int(rect.bottom) - int(rect.top)
    if width <= 0 or height <= 0 or width > 1600 or height > 900:
        return False
    style = int(user32.GetWindowLongW(hwnd, -16)) & 0xFFFFFFFF
    return (style & 0x00040000) == 0


def _contains_text_field(hwnd: int) -> bool:
    ctypes, wintypes, user32, _kernel32, enum_proc = _win32()
    found = False

    @enum_proc
    def visit(child, _lparam):
        nonlocal found
        class_name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(child, class_name, 64)
        if class_name.value == "Edit":
            found = True
            return False
        return True

    user32.EnumChildWindows(hwnd, visit, 0)
    return found


def _window_text(user32, ctypes, hwnd: int) -> str:
    length = int(user32.GetWindowTextLengthW(hwnd) or 0)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _dismiss_dialog(hwnd: int) -> dict | None:
    ctypes, wintypes, user32, _kernel32, enum_proc = _win32()
    buttons: list[tuple[int, str, int]] = []

    @enum_proc
    def visit(child, _lparam):
        class_name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(child, class_name, 64)
        if class_name.value == "Button":
            buttons.append((
                int(user32.GetDlgCtrlID(child)),
                _window_text(user32, ctypes, child),
                int(child),
            ))
        return True

    user32.EnumChildWindows(hwnd, visit, 0)
    # WM_USER + 0. The high word is DC_HASDEFID (0x534B) when a default exists.
    default_message = int(user32.SendMessageW(hwnd, 0x0400, 0, 0) or 0)
    default_id = default_message & 0xFFFF if (default_message >> 16) == 0x534B else None
    chosen = select_dismiss_control(
        [(control_id, caption) for control_id, caption, _child in buttons],
        default_id,
    )
    target = None
    caption = ""
    if chosen is not None:
        target = next((child for control_id, text, child in buttons if control_id == chosen), None)
        caption = next((text for control_id, text, _child in buttons if control_id == chosen), "")
    if target is not None:
        user32.SendMessageW(target, 0x00F5, 0, 0)
        return _dismissal(user32, ctypes, hwnd, caption or "OK", chosen, "button")
    # The 16-bit wrapper often draws a real Yes/No box without exposing Button
    # children. MessageBox still honors these command ids: Yes, then OK.
    for control_id, label in ((6, "Yes"), (1, "OK"), (11, "Continue")):
        child = user32.GetDlgItem(hwnd, control_id)
        if child:
            user32.SendMessageW(child, 0x00F5, 0, 0)
            return _dismissal(user32, ctypes, hwnd, label, control_id, "dialog-item")
        user32.SendMessageW(hwnd, 0x0111, control_id, 0)
        if not user32.IsWindowVisible(hwnd):
            return _dismissal(user32, ctypes, hwnd, label, control_id, "command")
    user32.PostMessageW(hwnd, 0x0100, 0x0D, 0)
    user32.PostMessageW(hwnd, 0x0101, 0x0D, 0)
    return _dismissal(user32, ctypes, hwnd, "Enter", 0x0D, "key")


def _dismissal(user32, ctypes, hwnd: int, button: str, control_id: int, method: str) -> dict:
    return {
        "title": _window_text(user32, ctypes, hwnd) or "Stars!",
        "button": button,
        "control_id": control_id,
        "method": method,
        "hwnd": hwnd,
    }


if __name__ == "__main__":
    main()
