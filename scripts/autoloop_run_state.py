"""Which run is going, and where its numbers come from.

Extracted from `autoloop_tui` to keep that module under the 400-line filesize
gate. Strictly a reader of files on disk — the dashboard, the overlay and the
text fallback all take their answer from here rather than each deciding for
itself what "a run is going" means.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

# Agents mode runs the queue in separate `claude -p` processes; chat mode runs
# it in the conversation the human is watching. They report different things,
# so a viewer has to know which one it is looking at.
MODE_AGENTS = "agents"
MODE_CHAT = "chat"

_AGENTS_MARKER = ".autoloop.run"
_CHAT_MARKER = ".chat-loop.json"
_WATCH_LOG = "chat-watch.log"
# The last step of a cleanup cycle; its landing is what makes the cycle done.
_CLEANUP_DONE_MARK = "/start выполнен"


_live_percent_cache: dict[str, tuple[float, int, float | None]] = {}


def live_percent(project_dir: str) -> float | None:
    """Context fill as it is now, or None when it cannot be read.

    The stored reading is written by the Stop hook — once per agent turn. A
    turn can run for an hour while the window keeps filling, so the window
    showed 39.6% against a conversation actually sitting at 47.9%. The sensor's
    own reader answers on demand; asking it costs a bounded tail read.

    Cached on (mtime, size): a window repaints about once a second, and the
    transcript only changes when the conversation does.
    """
    try:
        import autoloop_presence as presence
        from autoloop.context import percent_full, read_context_usage
    except ImportError:
        return None

    path = presence.transcript_path(project_dir)
    if not path:
        return None
    try:
        stat = os.stat(path)
    except OSError:
        return None

    cached = _live_percent_cache.get(path)
    if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        return cached[2]

    usage = read_context_usage(path)
    percent = percent_full(usage.get("tokens")) if usage.get("ok") else None
    _live_percent_cache[path] = (stat.st_mtime, stat.st_size, percent)
    return percent


def chat_run(project_dir: str) -> dict:
    """The in-chat run's declaration: direction + started_at, or empty.

    The two modes leave two different marks. Reading only the agents one made
    the window announce «прогон не запущен» through an entire in-chat run,
    while showing a live context reading beside that caption and an iteration
    count left over from a run two days earlier.
    """
    path = os.path.join(project_dir, ".tausik", _CHAT_MARKER)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def mode(project_dir: str) -> str | None:
    """Which run is declared, if any. Agents mode wins when both marks exist:
    it is the one with processes actually turning."""
    if os.path.exists(os.path.join(project_dir, ".tausik", _AGENTS_MARKER)):
        return MODE_AGENTS
    return MODE_CHAT if chat_run(project_dir) else None


def stop_requested(project_dir: str) -> bool:
    return os.path.exists(os.path.join(project_dir, ".tausik", ".autoloop.stop"))


def cleanups_done(project_dir: str) -> int:
    """Completed cleanup cycles, from the watcher's own log.

    The in-chat mode has no iterations to count — work happens in one
    conversation. What repeats is the cleanup: checkpoint, clear, start.
    """
    path = os.path.join(project_dir, ".tausik", _WATCH_LOG)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return sum(1 for line in f if _CLEANUP_DONE_MARK in line)
    except OSError:
        return 0


def chat_elapsed(project_dir: str) -> int:
    """How long the declared in-chat run has been going."""
    started = chat_run(project_dir).get("started_at")
    if not started:
        return 0
    try:
        begin = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
    except ValueError:
        return 0
    return max(0, int((datetime.now(timezone.utc) - begin).total_seconds()))
