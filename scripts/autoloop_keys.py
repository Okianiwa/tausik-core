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
from contextlib import contextmanager
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

ENTER = "\r"
# The pause between the text and the Enter that sends it. Not cosmetic: a chat
# reading its input in one gulp treats a large block ending in Enter as pasted
# multi-line text, and the Enter becomes a newline inside the draft instead of
# sending it. Short commands survived this by being short — the first long line
# the watcher typed sat in the input box unsent.
SUBMIT_DELAY = 0.4
INPUT_LINES = 12  # enough of the screen bottom to hold a multi-line draft

LOG_FILE = os.path.join(".tausik", "chat-watch.log")


def journal(message: str) -> None:
    """Record one keystroke delivery in the project's watch log.

    Writing into another process's console left no trace anywhere, so when a
    human lost their half-typed input the question "did anything type into this
    window?" had no answer — the watcher's own log only ever showed commands it
    had already confirmed. An unlogged write is an unfalsifiable suspect.
    """
    path = os.path.join(os.getcwd(), LOG_FILE)
    if not os.path.isdir(os.path.dirname(path)):
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {message}\n")
    except OSError:
        pass


def readable(text: str) -> str:
    """Control characters named, so the log shows Esc as Esc."""
    names = {"\x1b": "<Esc>", "\r": "<Enter>", "\n": "<Enter>"}
    return "".join(names.get(char, char) for char in text)


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


def key_records(text: str) -> list:
    """One key-down/key-up pair per character.

    Both halves matter: a console reading key events sees a press that never
    ended as a key still held down.

    Enter is not a special case here — `key_records(ENTER)` produces exactly
    the keystroke a human's Return key does, and sending it is a separate
    decision made by the caller.
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


@contextmanager
def attached(k32, pid: int):
    """Borrow the console of `pid`, and give ours back whatever happens.

    The `finally` is the point: a failure between attach and detach leaves this
    process holding somebody else's console, and everything it prints from then
    on lands in their window.
    """
    k32.FreeConsole()
    if not k32.AttachConsole(int(pid)):
        code = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0
        _reattach(k32)
        yield None, f"AttachConsole не удался (код {code})"
        return
    try:
        yield k32, ""
    finally:
        k32.FreeConsole()
        _reattach(k32)


def _write_keys(k32, pid: int, records: list) -> tuple[bool, str]:
    """One attach → write → detach. Returns (written, reason).

    A separate attachment per write rather than one long one: between the text
    and the Enter this process would otherwise hold a human's console for
    SUBMIT_DELAY, and everything it printed meanwhile would land in their
    window.
    """
    if not records:
        return True, ""
    buffer = (INPUT_RECORD * len(records))(*records)
    written = wintypes.DWORD(0)
    with attached(k32, pid) as (console, denied):
        if console is None:
            return False, denied
        handle = console.CreateFileW(
            "CONIN$",
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if handle in (None, 0, INVALID_HANDLE):
            return False, "CONIN$ не открылся"
        ok = bool(console.WriteConsoleInputW(handle, buffer, len(records), ctypes.byref(written)))
        console.CloseHandle(handle)
        return ok, "" if ok else "WriteConsoleInput вернул ошибку"


def send_to_console(pid: int, text: str, submit: bool = True, sleep=time.sleep) -> tuple[bool, str]:
    """Push `text` into the console owned by `pid`. Returns (sent, reason).

    The text and the Enter that sends it are two writes, never one block —
    see SUBMIT_DELAY for what one block costs.

    Refuses on a PID that is not running: attaching to a recycled PID would
    type into a stranger. A failed text write cancels the Enter — pressing
    Return over an input line whose contents are now unknown would send
    somebody else's draft.

    Every outcome is journalled, including the refusals and the Enter as its
    own line. This is the only record that a key was ever pushed into a human's
    window; without it, an input line that emptied itself has no way to
    accuse — or clear — this code.
    """
    label = f"клавиши → pid={pid} «{readable(text)}»"
    k32 = kernel32()
    if k32 is None:
        journal(f"{label}: Windows console API недоступен")
        return False, "Windows console API недоступен"
    if not pid_exists(pid):
        journal(f"{label}: процесс не найден")
        return False, f"процесс {pid} не найден — писать некуда"

    ok, reason = _write_keys(k32, pid, key_records(text))
    journal(f"{label}: {reason or 'отправлено'}")
    if not submit or not ok:
        return ok, reason

    sleep(SUBMIT_DELAY)
    ok, reason = _write_keys(k32, pid, key_records(ENTER))
    journal(f"клавиши → pid={pid} «<Enter>»: {reason or 'отправлено'}")
    return ok, reason


def _reattach(k32) -> None:
    """Get our own output back. Without this everything printed afterwards
    goes to somebody else's window — or nowhere."""
    try:
        k32.AttachConsole(-1)  # ATTACH_PARENT_PROCESS
    except Exception:  # noqa: BLE001 — ctypes surfaces any console failure as a bare Exception; losing our own console is not worth crashing over
        pass


class COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [
        ("Left", ctypes.c_short),
        ("Top", ctypes.c_short),
        ("Right", ctypes.c_short),
        ("Bottom", ctypes.c_short),
    ]


class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [
        ("dwSize", COORD),
        ("dwCursorPosition", COORD),
        ("wAttributes", wintypes.WORD),
        ("srWindow", SMALL_RECT),
        ("dwMaximumWindowSize", COORD),
    ]


def console_text(pid: int, lines: int = INPUT_LINES):
    """The bottom `lines` of another console's screen, or None if unreadable.

    The one signal a transcript cannot give. Its mtime moves only when a turn
    is sent, so somebody a minute into a long message looks exactly like
    somebody who walked away — and that is the state in which this mechanism
    wipes an input line that was being used.

    None means "could not look", never "nothing there": the caller must not
    read a failed screen read as an empty one.
    """
    k32 = kernel32()
    if k32 is None or not pid_exists(pid):
        return None
    with attached(k32, pid) as (console, _denied):
        if console is None:
            return None
        try:
            return _screen_tail(console, lines)
        except Exception:  # noqa: BLE001 — ctypes raises anything; an unreadable screen is an answer, not a crash
            return None


def _screen_tail(k32, lines: int):
    """Read the visible window's last `lines` rows through CONOUT$."""
    handle = k32.CreateFileW(
        "CONOUT$",
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    if handle in (None, 0, INVALID_HANDLE):
        return None
    try:
        info = CONSOLE_SCREEN_BUFFER_INFO()
        if not k32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
            return None
        width = info.srWindow.Right - info.srWindow.Left + 1
        if width <= 0:
            return None
        rows, read = [], wintypes.DWORD(0)
        buffer = ctypes.create_unicode_buffer(width + 1)
        top = max(info.srWindow.Top, info.srWindow.Bottom - lines + 1)
        for y in range(top, info.srWindow.Bottom + 1):
            if not k32.ReadConsoleOutputCharacterW(
                handle,
                buffer,
                width,
                COORD(info.srWindow.Left, y),
                ctypes.byref(read),
            ):
                return None
            rows.append(str(buffer[: read.value]).rstrip())
        return "\n".join(rows)
    finally:
        k32.CloseHandle(handle)


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
    table: dict[int, tuple[int, str]] = {}
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
    return [pid for pid, (_parent, name) in process_table().items() if name == "claude.exe"]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("использование: autoloop_keys.py <pid> <текст>")
        print(f"запущенные чаты: {find_chat_pids()}")
        return 0
    try:
        pid = int(argv[0])
    except ValueError:
        print(f"не pid: {argv[0]!r}")
        return 2
    text = " ".join(argv[1:])
    sent, reason = send_to_console(pid, text)
    # Printed after the console is back: while attached, this would land in
    # the other window.
    print(f"[keys] {'отправлено' if sent else 'не отправлено'} pid={pid} {reason}".rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
