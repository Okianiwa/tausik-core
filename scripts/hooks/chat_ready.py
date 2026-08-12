"""Stop hook: tell the wrapper that the input line is free.

The driver types into the same stream the human does, so it must never type
while a turn is running or a permission dialog is open — an Enter sent then
answers the dialog. A timer cannot know when that moment is; the host can, and
this is where it says so.

Silent outside the wrapper: without TAUSIK_CHAT the flag would be litter that
nobody reads and nobody clears.
"""

import os
import sys


def flag_path(project_dir: str) -> str:
    return os.path.join(project_dir, ".tausik", ".chat.ready")


def watcher_running(project_dir: str) -> bool:
    """Is anyone waiting for this flag?

    The wrapper announced itself with TAUSIK_CHAT; the watcher cannot — it
    starts after the session and sets no variables in it. Its lock file is the
    signal instead. Without this the flag never appears and the cleanup waits
    forever for a turn that already ended.
    """
    return os.path.exists(os.path.join(project_dir, ".tausik", ".chat-watch.lock"))


def main() -> int:
    try:
        sys.stdin.read()  # the hook payload; drained so the host's pipe closes
    except Exception:  # noqa: BLE001 — a hook that raises while draining stdin breaks the turn it was meant to observe
        pass
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    if os.environ.get("TAUSIK_CHAT") != "1" and not watcher_running(project_dir):
        return 0
    try:
        with open(flag_path(project_dir), "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass  # a missed flag delays a cleanup; a crashed hook breaks the turn
    return 0


if __name__ == "__main__":
    sys.exit(main())
