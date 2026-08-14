"""Has this session run out of something? Asked by both loops.

The chat watcher asks in order to close the session before wiping the window;
the autonomous run asks in order not to spend an iteration on a task that
cannot be started. Same question, same answer — so it lives in one place
rather than being reimplemented on each side with its own idea of which
budgets exist.

Read through the CLI, not the database: the limits it reports already account
for `session extend`, and recomputing them here would mean a second opinion
that can disagree with the gate doing the actual blocking.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

STATUS_TIMEOUT = 30.0
CREATE_NO_WINDOW = 0x08000000  # or the desktop blinks on every poll

CLOCK_LINE = re.compile(r"active\s+(\d+)m\s*/\s*(\d+)m")
CAPACITY_LINE = re.compile(r"Capacity:\s*(\d+)/(\d+)\s+used.*?(-?\d+)\s+remaining")


def status_text(project_dir: str):
    """What the CLI says about the project, or None when it could not be asked."""
    cli = os.path.join(os.path.dirname(os.path.abspath(__file__)), "project.py")
    try:
        out = subprocess.run(
            [sys.executable, cli, "status"],
            stdin=subprocess.DEVNULL,  # nothing to read; an inherited stdin can hang the run
            cwd=project_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=STATUS_TIMEOUT,
            creationflags=CREATE_NO_WINDOW,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return out.stdout or ""


def session_spent(project_dir: str):
    """Which budget has run out, named for the log — or None while there is room.

    Two budgets, not one. The clock is the famous one, but in practice it is
    capacity that stops work first: `task start` refuses with "budget=45
    exceeds remaining -84/200" while the session still has hours on it.

    Anything unreadable answers "there is room": stopping a run that could have
    continued costs a night of work, while stopping one cycle late costs one
    cycle.
    """
    text = status_text(project_dir)
    if text is None:
        return None
    clock = CLOCK_LINE.search(text)
    if clock and int(clock.group(1)) >= int(clock.group(2)):
        return f"время {clock.group(1)}/{clock.group(2)} мин"
    room = CAPACITY_LINE.search(text)
    if room and (int(room.group(1)) >= int(room.group(2)) or int(room.group(3)) <= 0):
        return f"ёмкость {room.group(1)}/{room.group(2)}, свободно {room.group(3)}"
    return None
