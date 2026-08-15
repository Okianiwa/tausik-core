"""Putting the run window up — the one piece both modes share.

Split out of the supervisor because the chat mode needs it too, and importing
the supervisor from a command that only declares a run would drag the whole
child/git/journal stack in behind it.

Everything about the window itself lives in `autoloop_overlay`; this module
only decides whether to start one and starts it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# The window is a subcommand of the supervisor rather than a script of its own:
# `autoloop_run.py overlay` is also what a human types, and one entry point
# means the window a run opens is the window a human opens.
ENTRY_POINT = Path(__file__).resolve().parent / "autoloop_run.py"


def under_test() -> bool:
    """Is this interpreter running a test suite?

    Asked before opening a window, because the window is the one thing here
    that deliberately outlives its parent. Measured: three suite runs left 58
    live tkinter windows on a desktop — the spawn sits several calls below any
    test that starts a run, and every one of those windows survived pytest
    exactly as designed.

    The refusal lives in the code rather than in a fixture because fixtures do
    not travel: a project receives a flat `tests/` copy without the library's
    conftest, and the library is deployed to nine of them.
    """
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules


def spawn_overlay(project_dir: Path | str) -> str:
    """Put the window up for a run that has just been declared.

    Detached the way the watcher is, for the reason measured there: an
    unredirected child keeps the parent's pipe open, and whoever started the
    run then hears nothing from it until it dies.

    The process is not kept: nothing waits on the window and nothing takes it
    down at the end — a cat asleep in the corner is the report.

    Returns a message to log, or "" when a window went up, was already up, or
    was deliberately not started.
    """
    import autoloop_presence as presence

    if under_test():
        return ""
    if presence.overlay_is_open(str(project_dir)):
        return ""
    # Detached on Windows so the window is not taken down with its parent: it
    # is meant to still be there, cat asleep, once the run has finished.
    flags = (0x00000008 | 0x00000200) if os.name == "nt" else 0  # DETACHED | NEW_GROUP
    try:
        subprocess.Popen(
            [sys.executable, str(ENTRY_POINT), "overlay"],
            cwd=str(project_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except (OSError, ValueError) as e:
        return str(e)
    return ""
