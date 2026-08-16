"""A cleanup somebody asked for, as opposed to one the mechanism decided on.

The watcher cleans when three things line up: the window is full, the chat has
been quiet for 45 seconds, and no refusal is still in its cooldown. All three
are guesses at the same question — will a person mind if this happens now. A
person who types «почисти» has answered that question directly, so a request
overrides all three rather than waiting them out.

Background work is the one thing it does not override. `/clear` in the middle
of a running job loses whatever has not landed yet, and the human asking for a
cleanup is looking at a full window, not at the agent's current step — they
cannot see what they would be interrupting.

A file rather than a signal, for the same reason the run declaration is one:
whoever leaves it needs no PID, no channel, and no healthy watcher on the other
end. If the watcher is not up, nothing happens and the request keeps waiting —
which is why the command that writes it says so out loud.
"""

from __future__ import annotations

import os

REQUEST_FILE = os.path.join(".tausik", ".chat-clean.request")


def path(project_dir: str) -> str:
    return os.path.join(project_dir, REQUEST_FILE)


def request(project_dir: str) -> bool:
    """Leave the request. False when it could not be written.

    Never swallowed: the caller's whole job is to tell a person whether a
    cleanup is coming, and a promise made over a file that does not exist is
    worse than the full window it was meant to fix.
    """
    try:
        with open(path(project_dir), "w", encoding="utf-8") as f:
            f.write("")
    except OSError:
        return False
    return True


def requested(project_dir: str) -> bool:
    return os.path.exists(path(project_dir))


def drop(project_dir: str) -> None:
    """Consume the request — called BEFORE the first keystroke, never after.

    A cleanup can die halfway through: the console stops reading, `/checkpoint`
    leaves no trace, the run is withdrawn mid-sequence. Dropped afterwards, the
    request survives every one of those and the next tick starts the sequence
    again, and the tick after that, for as long as the watcher lives.
    """
    try:
        os.unlink(path(project_dir))
    except OSError:
        pass


def due(project_dir: str, busy: bool) -> bool:
    """Should this tick run it? Fill, silence and cooldown are already answered
    by the person having asked; work still running is not."""
    return requested(project_dir) and not busy
