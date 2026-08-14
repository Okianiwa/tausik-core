#!/usr/bin/env python3
"""Stop hook — record context fill and task completion state.

Measures only. Deciding whether to end the session belongs to exit_guard.py;
keeping the two apart means the measurement keeps running (and stays
observable) even with the autonomous loop switched off.

Always exits 0 and prints nothing on stdout: a Stop hook that writes to stdout
without a decision field pollutes the turn, and one that raises swallows the
agent's output entirely.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoloop.context import percent_full, read_context_usage  # noqa: E402
from autoloop.state import load_config, read_task_state, write_state  # noqa: E402


def _run_declared(project_dir: str) -> bool:
    """Kept import-local: a Stop hook that fails on an import stops the turn."""
    try:
        import autoloop_chat_cycle

        return autoloop_chat_cycle.run_declared(project_dir)
    except ImportError:
        return False


def build_payload(project_dir: str, transcript_path: str, session_id: str = "") -> dict:
    config = load_config(project_dir)
    usage = read_context_usage(transcript_path)
    task_state, task_slug = read_task_state(project_dir)

    return {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session_id": session_id,
        "percent": percent_full(usage["tokens"], config["context_window"]),
        "tokens": usage["tokens"],
        "context_window": config["context_window"],
        "model": usage["model"],
        "task_slug": task_slug,
        "task_state": task_state,
        "reason": usage["reason"],
    }


def main() -> int:
    if os.environ.get("TAUSIK_SKIP_HOOKS"):
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    if not os.path.isdir(os.path.join(project_dir, ".tausik")):
        return 0
    # Measuring is watching. Outside a declared run nobody asked to be watched,
    # and the only consumer of these readings is the watcher that is not
    # running either.
    if not _run_declared(project_dir):
        return 0

    session_id = str(payload.get("session_id") or "")
    state = build_payload(project_dir, payload.get("transcript_path") or "", session_id)

    # exit_requested is owned by exit_guard.py and must survive a sensor
    # rewrite — dropping it would re-arm a block that already fired.
    previous = payload.get("_previous_state")
    if not isinstance(previous, dict):
        from autoloop.state import read_state

        previous = read_state(project_dir, session_id)
    if previous.get("exit_requested"):
        state["exit_requested"] = True
        state["exit_kind"] = previous.get("exit_kind")

    write_state(project_dir, session_id, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
