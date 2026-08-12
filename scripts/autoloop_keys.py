"""Type into a chat that is already running, without standing between it and
its terminal.

The wrapper approach failed on principle, not on bugs (dead end #17): a
full-screen TUI negotiates with its terminal through a dozen protocols, and a
proxy has to reproduce every one of them. So do not proxy. Windows lets one
process attach to another's console and push key events straight into its
input buffer — the chat reads them exactly as it reads a keyboard, and nothing
touches its drawing.

This is not keyboard emulation. `SendInput` types into whatever window has
focus and can land anywhere; `WriteConsoleInput` addresses one console by
handle. Wrong window is not a failure mode here.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

# Console API constants.
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
KEY_EVENT = 0x0001
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
INVALID_HANDLE = ctypes.c_void_p(-1).value

# Control characters a console expects as keys, not as text.
VK_BY_CHAR = {"\r": VK_RETURN, "\n": VK_RETURN, "\x1b": VK_ESCAPE}

SUBMIT_DELAY = 0.4


class _CHAR(ctypes.Union):
    _fields_ = [("UnicodeChar", wintypes.WCHAR), ("AsciiChar", ctypes.c_char)]


class _KEY_EVENT(ctypes.Structure):
    _fields_ = [
        ("bKeyDown", wintypes.BOOL),
        ("wRepeatCount", wintypes.WORD),
        ("wVirtualKeyCode", wintypes.WORD),
        ("wVirtualScanCode", wintypes.WORD),
        ("uChar", _CHAR),
        ("dwControlKeyState", wintypes.DWORD),
    ]


class _EVENT(ctypes.Union):
    _fields_ = [("KeyEvent", _KEY_EVENT)]


class INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", wintypes.WORD), ("Event", _EVENT)]


def kernel32():
    """The console API, or None where there is none (tests, non-Windows)."""
    try:
        return ctypes.windll.kernel32
    except (AttributeError, OSError):
        return None


def key_records(text: str, submit: bool = True) -> list:
    """One key-down/key-up pair per character, Enter last.

    Both halves matter: a console reading key events sees a press that never
    ended as a key still held down.
    """
    records = []
    for char in text:
        for down in (True, False):
            record = INPUT_RECORD()
            record.EventType = KEY_EVENT
            record.Event.KeyEvent.bKeyDown = down
            record.Event.KeyEvent.wRepeatCount = 1
            # Esc and Enter must carry their virtual key code: as bare
            # characters a TUI treats them as text, not as the keys they are.
            record.Event.KeyEvent.wVirtualKeyCode = VK_BY_CHAR.get(char, 0)
            record.Event.KeyEvent.wVirtualScanCode = 0
            record.Event.KeyEvent.uChar.UnicodeChar = char
            record.Event.KeyEvent.dwControlKeyState = 0
            records.append(record)
    if submit:
        for down in (True, False):
            record = INPUT_RECORD()
            record.EventType = KEY_EVENT
            record.Event.KeyEvent.bKeyDown = down
            record.Event.KeyEvent.wRepeatCount = 1
            record.Event.KeyEvent.wVirtualKeyCode = VK_RETURN
            record.Event.KeyEvent.wVirtualScanCode = 0
            record.Event.KeyEvent.uChar.UnicodeChar = "\r"
            record.Event.KeyEvent.dwControlKeyState = 0
            records.append(record)
    return records


PROCESS_QUERY_LIMITED = 0x1000
STILL_ACTIVE = 259
TH32CS_SNAPPROCESS = 0x00000002


def pid_exists(pid) -> bool:
    """Deliberately not `tasklist`: the watcher asks this every couple of
    seconds, and every external command flashes a console window on screen.
    A user watching their desktop blink is a bug, even if the logic is right.
    """
    k32 = kernel32()
    if k32 is None:
        return False
    try:
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED, False, int(pid))
    except (ValueError, TypeError):
        return False
    if not handle:
        return False
    code = wintypes.DWORD(0)
    ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
    k32.CloseHandle(handle)
    return bool(ok) and code.value == STILL_ACTIVE


def send_to_console(pid: int, text: str, submit: bool = True) -> tuple[bool, str]:
    """Push `text` into the console owned by `pid`. Returns (sent, reason).

    Refuses on a PID that is not running: attaching to a recycled PID would
    type into a stranger.
    """
    k32 = kernel32()
    if k32 is None:
        return False, "Windows console API недоступен"
    if not pid_exists(pid):
        return False, f"процесс {pid} не найден — писать некуда"

    records = key_records(text, submit)
    buffer = (INPUT_RECORD * len(records))(*records)
    written = wintypes.DWORD(0)

    k32.FreeConsole()
    if not k32.AttachConsole(int(pid)):
        code = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0
        _reattach(k32)
        return False, f"AttachConsole не удался (код {code})"

    handle = k32.CreateFileW(
        "CONIN$",
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    ok = False
    reason = ""
    if handle in (None, 0, INVALID_HANDLE):
        reason = "CONIN$ не открылся"
    else:
        ok = bool(
            k32.WriteConsoleInputW(handle, buffer, len(records), ctypes.byref(written))
        )
        reason = "" if ok else "WriteConsoleInput вернул ошибку"
        k32.CloseHandle(handle)

    k32.FreeConsole()
    _reattach(k32)
    return ok, reason


def _reattach(k32) -> None:
    """Get our own output back. Without this everything printed afterwards
    goes to somebody else's window — or nowhere."""
    try:
        k32.AttachConsole(-1)  # ATTACH_PARENT_PROCESS
    except Exception:  # noqa: BLE001 — ctypes surfaces any console failure as a bare Exception; losing our own console is not worth crashing over
        pass


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


def process_table() -> dict:
    """{pid: (parent_pid, exe_name)} from one snapshot — no child processes,
    no flashing windows."""
    table = {}
    k32 = kernel32()
    if k32 is None:
        return table
    try:
        snapshot = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot == -1:
            return table
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        ok = k32.Process32First(snapshot, ctypes.byref(entry))
        while ok:
            table[int(entry.th32ProcessID)] = (
                int(entry.th32ParentProcessID),
                entry.szExeFile.decode("utf-8", "replace").lower(),
            )
            ok = k32.Process32Next(snapshot, ctypes.byref(entry))
        k32.CloseHandle(snapshot)
    except Exception:  # noqa: BLE001 — a snapshot that fails for any reason means "no process table", never a crash in a hook
        return {}
    return table


def find_chat_pids() -> list[int]:
    return [
        pid for pid, (_parent, name) in process_table().items() if name == "claude.exe"
    ]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("использование: autoloop_keys.py <pid> <текст>")
        print(f"запущенные чаты: {find_chat_pids()}")
        return 0
    pid, text = argv[0], " ".join(argv[1:])
    sent, reason = send_to_console(pid, text)
    # Printed after the console is back: while attached, this would land in
    # the other window.
    print(
        f"[keys] {'отправлено' if sent else 'не отправлено'} pid={pid} {reason}".rstrip()
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
