#!/usr/bin/env python3
"""PreToolUse hook: block Write/Edit if no active task in TAUSIK.

v1.4: direct SQLite SELECT replaces the previous subprocess + 5s timeout
shape. Two reasons:

  1. **Speed.** A subprocess CLI call costs 100-300 ms per Write/Edit on
     Windows; pure SQLite query is sub-millisecond. Editor-heavy sessions
     used to feel sluggish.
  2. **Reliability.** A subprocess that fails (PowerShell quirk, locked
     venv, transient OSError) used to silently let edits through.

     Since decision #58 the default is fail-SECURE: a DB error blocks the
     write. The gate only reaches that branch when `.tausik/tausik.db`
     exists, so the error means real breakage rather than an uninitialised
     project, and a guard that silently allows what it is meant to gate is
     worse than a loud stop. A locked DB — the one common, self-resolving
     failure — is retried before blocking. `TAUSIK_HOOK_FAIL_OPEN=1`
     restores the old permissive behaviour.

Exit codes: 0 = allow, 2 = block.
Receives JSON on stdin with tool_name, tool_input.
Skipped via TAUSIK_SKIP_HOOKS=1 env var.
"""

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import is_tausik_project  # noqa: E402


# Budget for the two SELECT attempts, in seconds. Both numbers are small on
# purpose: SQLite's busy handler OVERSHOOTS the nominal timeout by ~1.5x
# (measured on this host: 1.5 -> 2.48s, 2.0 -> 3.20s, 5.0 -> 7.42s), and the
# hook itself is killed at the `timeout` set in bootstrap_hooks.py. A killed
# hook returns aborted, and the harness only blocks on exit code 2 — so
# overrunning the budget is a SILENT ALLOW, the exact opposite of the
# fail-secure guarantee. An earlier 2.0 + 5.0 pair took 10.65s against a
# 10s budget and lost the block in 3 runs out of 3.
# tests/test_task_gate_hook.py::test_db_timeout_budget_fits_hook_timeout
# keeps this under the configured timeout.
_DB_TIMEOUTS = (1.0, 2.0)
_SQLITE_OVERSHOOT = 1.6


def _query_active_task(db_path: str, timeout: float) -> bool:
    conn = sqlite3.connect(db_path, timeout=timeout)
    try:
        row = conn.execute("SELECT 1 FROM tasks WHERE status = 'active' LIMIT 1").fetchone()
        return row is not None
    finally:
        conn.close()


def _has_active_task(db_path: str) -> bool:
    """Direct SQLite SELECT — no subprocess.

    Returns True iff at least one row in `tasks` has status='active'.
    Retries once before giving up: a concurrent writer holding the lock is
    the one failure here that is both common and self-resolving, and under
    the fail-secure default a single unlucky timeout would otherwise block
    a legitimate edit. Both attempts together must stay inside the hook's
    own timeout — see _DB_TIMEOUTS.

    Raises sqlite3.Error if the retry fails too, so the caller can apply
    the fail-secure policy.
    """
    first, retry = _DB_TIMEOUTS
    try:
        return _query_active_task(db_path, timeout=first)
    except sqlite3.OperationalError:
        return _query_active_task(db_path, timeout=retry)


def _is_read_only_call(payload: dict) -> bool:
    """True for calls that cannot change a file.

    The matcher has to name windows-mcp FileSystem to catch `mode=write`,
    but that same tool also reads, lists and searches. Blocking those would
    stop plain research before a task is started — the rule is about
    changing code, not looking at it.
    """
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    mode = tool_input.get("mode")
    return isinstance(mode, str) and mode in {"read", "list", "search", "info"}


def main() -> int:
    if os.environ.get("TAUSIK_SKIP_HOOKS"):
        return 0

    # Unreadable payload is treated as a write: guessing "probably harmless"
    # in the guard's own parse failure is how gates go quiet.
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError, EOFError):
        payload = {}
    if isinstance(payload, dict) and _is_read_only_call(payload):
        return 0

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())

    if not is_tausik_project(project_dir):
        return 0

    db_path = os.path.join(project_dir, ".tausik", "tausik.db")
    if not os.path.exists(db_path):
        # Bootstrap-but-not-init: nothing to enforce yet.
        return 0

    # Fail-secure is the DEFAULT (decision #58). The DB file exists at this
    # point, so a failing SELECT means real breakage, not a fresh project —
    # and a guard that silently allows the action it is supposed to gate is
    # the exact defect class this hook exists to prevent. Opt out with
    # TAUSIK_HOOK_FAIL_OPEN=1 when a broken DB must not stop editing.
    fail_open = os.environ.get("TAUSIK_HOOK_FAIL_OPEN") == "1"

    try:
        active = _has_active_task(db_path)
    except sqlite3.Error as e:
        if fail_open:
            return 0
        print(
            f"BLOCKED: task gate could not query .tausik/tausik.db: {e}. "
            "Run `tausik doctor`. To edit anyway, set TAUSIK_HOOK_FAIL_OPEN=1 "
            "— that disables the no-code-without-a-task rule.",
            file=sys.stderr,
        )
        return 2
    except Exception as e:  # defensive — never bring down the host.
        if fail_open:
            return 0
        print(
            f"BLOCKED: task gate crashed: {e}. To edit anyway, set "
            "TAUSIK_HOOK_FAIL_OPEN=1 — that disables the gate.",
            file=sys.stderr,
        )
        return 2

    if active:
        return 0

    print(
        "BLOCKED: No active task. Start a task first:\n"
        "  Say 'начинай работу' then describe your task, or use /go.\n"
        "  TAUSIK requires a task before code changes (SENAR Rule 1).",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
