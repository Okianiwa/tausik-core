"""Typing into somebody else's console: the parts checkable without one.

The live half is in the task evidence — a chat started by an ordinary command
received `Ответь ровно: метка 7788` as a user message and answered it, with no
wrapper in between. What is testable here is the shape of the key events and
the refusals around them.
"""

import autoloop_keys as keys


def chars_of(records):
    """The characters a console would see, ignoring key-up halves."""
    return "".join(
        r.Event.KeyEvent.uChar.UnicodeChar for r in records if r.Event.KeyEvent.bKeyDown
    )


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
