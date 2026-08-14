"""Typing into somebody else's console: the parts checkable without one.

The live half is in the task evidence — a chat started by an ordinary command
received `Ответь ровно: метка 7788` as a user message and answered it, with no
wrapper in between. What is testable here is the shape of the key events and
the refusals around them.
"""

import autoloop_keys as keys
import pytest


def chars_of(records):
    """The characters a console would see, ignoring key-up halves."""
    return "".join(r.Event.KeyEvent.uChar.UnicodeChar for r in records if r.Event.KeyEvent.bKeyDown)


def test_every_key_is_pressed_and_released():
    """A console reading raw key events sees a press without a release as a
    key still held down."""
    records = keys.key_records("аб", submit=False)

    assert len(records) == 4
    assert [bool(r.Event.KeyEvent.bKeyDown) for r in records] == [True, False] * 2


def test_the_text_arrives_as_typed():
    assert chars_of(keys.key_records("Привет, мир", submit=False)) == "Привет, мир"


def test_enter_comes_last_and_is_a_real_return_key():
    """Submitting is a key with a virtual code, not just a carriage return
    character — the chat distinguishes them."""
    records = keys.key_records("да")

    assert records[-1].Event.KeyEvent.wVirtualKeyCode == keys.VK_RETURN
    assert records[-2].Event.KeyEvent.wVirtualKeyCode == keys.VK_RETURN
    assert bool(records[-2].Event.KeyEvent.bKeyDown) is True


def test_without_submit_nothing_is_sent():
    """Typing text and sending it are separate decisions: a command may need
    to sit in the input line unsent."""
    records = keys.key_records("черновик", submit=False)

    codes = {r.Event.KeyEvent.wVirtualKeyCode for r in records}
    assert keys.VK_RETURN not in codes


def test_every_record_is_a_key_event():
    for record in keys.key_records("x"):
        assert record.EventType == keys.KEY_EVENT


# --- refusals --------------------------------------------------------------


class FakeKernel:
    """Records API calls so a test can assert none happened."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            return 1

        return call


def test_a_dead_pid_is_refused_before_anything_is_written(monkeypatch):
    """AC negative: attaching to a recycled PID would type into a stranger."""
    fake = FakeKernel()
    monkeypatch.setattr(keys, "kernel32", lambda: fake)
    monkeypatch.setattr(keys, "pid_exists", lambda _pid: False)

    sent, reason = keys.send_to_console(999999, "не должно уйти")

    assert sent is False
    assert "не найден" in reason
    assert fake.calls == []  # not even an attach was attempted


def test_without_the_console_api_it_says_so(monkeypatch):
    monkeypatch.setattr(keys, "kernel32", lambda: None)

    sent, reason = keys.send_to_console(1234, "текст")

    assert sent is False
    assert "недоступен" in reason


def test_a_failed_attach_gives_our_own_console_back(monkeypatch):
    """AC negative: staying attached to somebody else's console means every
    later print lands in their window."""

    class RefusingKernel(FakeKernel):
        def AttachConsole(self, pid):
            self.calls.append(f"AttachConsole({pid})")
            return 0  # refused

    fake = RefusingKernel()
    monkeypatch.setattr(keys, "kernel32", lambda: fake)
    monkeypatch.setattr(keys, "pid_exists", lambda _pid: True)

    sent, _reason = keys.send_to_console(4321, "текст")

    assert sent is False
    assert "AttachConsole(-1)" in fake.calls  # reattached to our own


def test_a_write_that_raises_still_gives_the_console_back(monkeypatch):
    """AC negative: an exception between attach and detach used to leave this
    process holding a human's console."""

    class ExplodingKernel(FakeKernel):
        def CreateFileW(self, *_a):
            raise OSError("CONIN$ недоступен")

    fake = ExplodingKernel()
    monkeypatch.setattr(keys, "kernel32", lambda: fake)
    monkeypatch.setattr(keys, "pid_exists", lambda _pid: True)

    with pytest.raises(OSError):
        keys.send_to_console(4321, "текст")

    assert fake.calls[-2:] == ["FreeConsole", "AttachConsole"]  # ours, back


# --- the journal -----------------------------------------------------------


def journal_of(project_dir):
    path = project_dir / ".tausik" / "chat-watch.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_every_delivery_is_written_down(project_dir, monkeypatch):
    """The incident this exists for: a human's input line emptied itself, and
    nothing anywhere could say whether this code had typed into that window."""
    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(keys, "kernel32", lambda: FakeKernel())
    monkeypatch.setattr(keys, "pid_exists", lambda _pid: True)

    keys.send_to_console(20752, "/checkpoint")

    line = journal_of(project_dir)
    assert "pid=20752" in line
    assert "/checkpoint" in line
    assert "отправлено" in line


def test_esc_is_named_rather_than_written_raw(project_dir, monkeypatch):
    """An escape character in a log file is invisible — and Esc is the exact
    keystroke suspected of wiping a draft."""
    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(keys, "kernel32", lambda: FakeKernel())
    monkeypatch.setattr(keys, "pid_exists", lambda _pid: True)

    keys.send_to_console(20752, "\x1b", submit=False)

    assert "<Esc>" in journal_of(project_dir)


def test_a_refused_delivery_is_written_down_too(project_dir, monkeypatch):
    """A refusal is evidence as much as a success: it is what tells the
    investigation this process was not the one typing."""
    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(keys, "kernel32", lambda: FakeKernel())
    monkeypatch.setattr(keys, "pid_exists", lambda _pid: False)

    keys.send_to_console(999999, "/clear")

    assert "не найден" in journal_of(project_dir)


def test_a_project_without_tausik_is_not_littered(tmp_path, monkeypatch):
    """The CLI runs from anywhere; a log file must not appear in a stranger's
    working directory."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(keys, "kernel32", lambda: FakeKernel())
    monkeypatch.setattr(keys, "pid_exists", lambda _pid: True)

    keys.send_to_console(20752, "/start")

    assert list(tmp_path.iterdir()) == []


# --- reading the input line ------------------------------------------------


class ScreenKernel(FakeKernel):
    """A console showing fixed rows. `byref(x)._obj` is how a test reaches the
    structure the real API would fill in."""

    def __init__(self, rows):
        super().__init__()
        self.rows = rows

    def GetConsoleScreenBufferInfo(self, _handle, ref):
        info = ref._obj
        info.srWindow.Left = 0
        info.srWindow.Top = 0
        info.srWindow.Right = 79
        info.srWindow.Bottom = len(self.rows) - 1
        return 1

    def ReadConsoleOutputCharacterW(self, _handle, buffer, _length, coord, read):
        text = self.rows[coord.Y]
        buffer.value = text
        read._obj.value = len(text)
        return 1


def test_the_bottom_of_the_screen_comes_back(monkeypatch):
    monkeypatch.setattr(keys, "kernel32", lambda: ScreenKernel(["выше", "> черновик"]))
    monkeypatch.setattr(keys, "pid_exists", lambda _pid: True)

    assert keys.console_text(20752) == "выше\n> черновик"


def test_an_unreadable_screen_is_none_not_empty(monkeypatch):
    """AC negative: a failed look must never read as "the input line is empty" —
    that is the reading on which this mechanism types."""

    class BlindKernel(FakeKernel):
        def GetConsoleScreenBufferInfo(self, *_a):
            return 0

    monkeypatch.setattr(keys, "kernel32", lambda: BlindKernel())
    monkeypatch.setattr(keys, "pid_exists", lambda _pid: True)

    assert keys.console_text(20752) is None


def test_a_screen_read_that_raises_is_none(monkeypatch):
    class ExplodingKernel(FakeKernel):
        def GetConsoleScreenBufferInfo(self, *_a):
            raise OSError("буфер исчез")

    fake = ExplodingKernel()
    monkeypatch.setattr(keys, "kernel32", lambda: fake)
    monkeypatch.setattr(keys, "pid_exists", lambda _pid: True)

    assert keys.console_text(20752) is None
    assert fake.calls[-2:] == ["FreeConsole", "AttachConsole"]  # ours, back
