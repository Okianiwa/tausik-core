#!/usr/bin/env python3
"""PostToolUse hook: write an activity event for every tool call.

Without this, `backend_session_metrics.compute_active_minutes` undercounts
because only a handful of code paths (verify, session_extend, task_done)
write to the `events` table. The 180-min SENAR Rule 9.2 active-time gate
would never trip on a session of pure Edit/Bash/Read work.

Single row per tool call (`entity_type='session'`, `action='tool_use'`).
No active task required — activity is per-session. Best-effort: silent on
any error so a tool call is never blocked.

Rows written by an unattended run carry `actor='autoloop'`, and the active-time
sum leaves them out. An iteration works inside whatever session is open — it
never opens one of its own — so a night of autonomous work was spending the
human's 180 minutes, and they came back in the morning to a session that would
not let them start a task. The mark is the only thing that can separate the
two, since by session id these events are indistinguishable.

Skipped via TAUSIK_SKIP_HOOKS=1.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

ACTOR_AUTOLOOP = "autoloop"


def _db_path(project_dir: str) -> str | None:
    path = os.path.join(project_dir, ".tausik", "tausik.db")
    return path if os.path.exists(path) else None


def _actor(project_dir: str) -> str | None:
    """'autoloop' for an unattended run, None for a human at the keyboard.

    Unreadable autonomy answers None: counting a run's activity as the human's
    costs them minutes, but mislabelling their own work as a run's would hide
    real time from the gate that protects them.
    """
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))
        from autoloop import autonomy

        return ACTOR_AUTOLOOP if autonomy.is_enabled(project_dir) else None
    except Exception:  # noqa: BLE001 — a hook must never fail on an import
        return None


def main() -> int:
    if os.environ.get("TAUSIK_SKIP_HOOKS"):
        return 0
    try:
        json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        pass

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    db = _db_path(project_dir)
    if not db:
        return 0

    try:
        conn = sqlite3.connect(db, timeout=2)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
                "INSERT INTO events(entity_type, entity_id, action, actor) "
                "VALUES ('session', 'agent', 'tool_use', ?)",
                (_actor(project_dir),),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — best-effort hook
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
