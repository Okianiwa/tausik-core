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

Receives JSON on stdin with tool_name, tool_input. `tool_input.file_path` is
read to decide JURISDICTION: an edit landing outside this project is allowed
without a task here, because this gate has no authority over another
repository. Everything it cannot classify stays gated — see
`target_is_outside_project`. (Until v1.8 this docstring promised the stdin read
while the code never performed it, and the gate blocked cross-repository edits.)

Skipped via TAUSIK_SKIP_HOOKS=1 env var.
"""

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (  # noqa: E402
    cli_invocation,
    edited_file_paths,
    gate_exclude_globs,
    is_tausik_project,
    path_is_excluded,
)


def target_is_outside_project(raw_stdin: str, project_dir: str) -> bool:
    """Whether this call edits a file TAUSIK has no authority over.

    An agent often has more than one repository open. The gate's warrant is
    "no code without a task IN THIS PROJECT"; it has no standing over a file in
    someone else's repository, does not know their tasks, and cannot judge their
    discipline. Refusing there leaves an agent with a choice between abandoning
    legitimate work and opening a FICTITIOUS task here to unblock an edit
    elsewhere — and a gate that is profitable to fake is a gate that gets faked,
    after which it stops protecting this project too.

    FAIL-CLOSED BY CONSTRUCTION. Every uncertain case returns False, which means
    "keep gating": unparseable stdin, absent tool_input, a missing or non-string
    path, or any path arithmetic that raises. The loosening applies only to a
    target proven to sit outside, never to one merely not proven inside.

    Containment is decided on realpath via commonpath, NOT startswith: with a
    plain prefix test a sibling directory sharing a prefix (``…/core-old`` next
    to ``…/core``) reads as inside, and a symlink pointing from outside into the
    project reads as outside — each the wrong answer in the dangerous direction.
    """
    try:
        payload = json.loads(raw_stdin) if raw_stdin.strip() else {}
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            return False
        # Every path field a writer can carry, not just the two built-in ones:
        # a serena edit names `relative_path`, a FileSystem move `destination`.
        # Reading only `file_path` here made those calls "not proven outside",
        # which gates another repository's files — the defect this guards.
        paths = edited_file_paths(tool_input)
        if not paths:
            return False
        # Relative paths belong to the project by definition of the cwd the hook
        # runs in, so they resolve against project_dir and stay gated. ALL of
        # them must be outside: one target inside keeps the gate on.
        root = os.path.realpath(project_dir)
        return all(
            os.path.commonpath([os.path.realpath(os.path.join(project_dir, p)), root]) != root
            for p in paths
        )
    except Exception:  # noqa: BLE001 — any failure means "not proven outside" => keep gating
        return False


def target_is_excluded(raw_stdin: str, project_dir: str) -> bool:
    """Whether every target of this call is declared harness bookkeeping.

    Rule 1 is about CODE. A checkpoint pointer or a compaction log lives inside
    the tree but is written by the harness for the harness, and gating it does
    not protect the project — it silently stops the bookkeeping while the agent
    reports success. What counts as bookkeeping is declared by the project, not
    guessed here: see `gate_exclude_globs`, which ships empty.

    FAIL-CLOSED like `target_is_outside_project`, and for the same reason: an
    unparseable payload, an absent tool_input or any path arithmetic that
    raises keeps the gate on, and ONE target that is not excluded keeps it on
    for the whole call.
    """
    try:
        globs = gate_exclude_globs(project_dir)
        if not globs:
            return False
        payload = json.loads(raw_stdin) if raw_stdin.strip() else {}
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            return False
        paths = edited_file_paths(tool_input)
        if not paths:
            return False
        return all(path_is_excluded(p, project_dir, globs) for p in paths)
    except Exception:  # noqa: BLE001 — any failure means "not proven bookkeeping" => keep gating
        return False


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
    # hook-stderr-encoding-locale-dependent: this hook's messages contain
    # non-ASCII, and their readability must not depend on how it was
    # launched. Local import: hooks/ is sys.path[0] only when run as a script.
    from _common import (
        emit_supervision_bypass,
        emit_supervision_degradation,
        force_utf8_io,
    )

    force_utf8_io()

    if os.environ.get("TAUSIK_SKIP_HOOKS"):
        emit_supervision_bypass(
            os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()), "skip_hooks", "task_gate"
        )
        return 0

    # Read stdin ONCE and unconditionally: it is a pipe, leaving it unread can
    # block the caller, and a SECOND read returns "" — which this gate reads as
    # "not proven outside", so every edit in a sibling repository was refused.
    # Both consumers below are served from this one read.
    try:
        raw_stdin = sys.stdin.read()
    except Exception:  # noqa: BLE001 — unreadable stdin must not weaken the gate
        raw_stdin = ""

    # Unreadable payload is treated as a write: guessing "probably harmless"
    # in the guard's own parse failure is how gates go quiet.
    try:
        payload = json.loads(raw_stdin) if raw_stdin.strip() else {}
    except (json.JSONDecodeError, ValueError, EOFError):
        payload = {}
    if isinstance(payload, dict) and _is_read_only_call(payload):
        return 0

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())

    if not is_tausik_project(project_dir):
        return 0

    if target_is_outside_project(raw_stdin, project_dir):
        return 0

    if target_is_excluded(raw_stdin, project_dir):
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
        # Fail-secure by default (fork): a DB error blocks the edit instead of
        # quietly allowing it. Opting out stays possible, and an opt-out that
        # drops the gate must stay countable — that counter is upstream's.
        if fail_open:
            emit_supervision_degradation(project_dir, "db_error", "task_gate", str(e))
            return 0
        print(
            f"BLOCKED: task gate could not query .tausik/tausik.db: {e}. "
            "Run `tausik doctor`. To edit anyway, set TAUSIK_HOOK_FAIL_OPEN=1 "
            "— that disables the no-code-without-a-task rule.",
            file=sys.stderr,
        )
        return 2
    except Exception as e:  # defensive — never bring down the host.  # noqa: BLE001 — best-effort: a hook must never break the tool call it guards
        if fail_open:
            emit_supervision_degradation(project_dir, "db_error", "task_gate", str(e))
            return 0
        print(
            f"BLOCKED: task gate crashed: {e}. To edit anyway, set "
            "TAUSIK_HOOK_FAIL_OPEN=1 — that disables the gate.",
            file=sys.stderr,
        )
        return 2

    if active:
        return 0

    # The most-read message in the framework. It used to point at `/go`, a
    # skill that does not exist, and otherwise offered only a Russian phrase —
    # to an audience the README addresses in English. Both are now concrete,
    # existing commands, and the CLI is spelled for the reader's shell.
    cli = cli_invocation()
    print(
        "BLOCKED: No active task. TAUSIK requires a task before code changes "
        "(SENAR Rule 1).\n"
        "  Create one:   /plan   (or describe the task and ask to start it)\n"
        f"  Resume one:   {cli} task list --status planning\n"
        f"                {cli} task start <slug>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
