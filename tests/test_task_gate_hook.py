"""r14-task-gate-secure — direct SQLite + failure policy.

Replaces subprocess-based check with sqlite3 SELECT. Since decision #58
the default on a DB error is fail-SECURE (block); TAUSIK_HOOK_FAIL_OPEN=1
restores the permissive behaviour.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys


_HOOK = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "scripts", "hooks", "task_gate.py")
)


def _make_tausik_project(tmp_path, *, with_active_task: bool, broken_db: bool = False):
    """Construct a minimal TAUSIK project layout in tmp_path."""
    tausik_dir = tmp_path / ".tausik"
    tausik_dir.mkdir()
    db = tausik_dir / "tausik.db"
    if broken_db:
        # Write garbage to make sqlite3 throw.
        db.write_bytes(b"not a sqlite db")
    else:
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE tasks (slug TEXT PRIMARY KEY, status TEXT)")
        if with_active_task:
            conn.execute("INSERT INTO tasks VALUES (?, ?)", ("t", "active"))
        else:
            conn.execute("INSERT INTO tasks VALUES (?, ?)", ("t", "planning"))
        conn.commit()
        conn.close()
    return tmp_path


def _run(env_extra: dict, cwd):
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(cwd)}
    # Drop hostile parent env first, then apply test overrides — otherwise
    # a parent-set TAUSIK_SKIP_HOOKS would short-circuit every test, and a
    # parent-set TAUSIK_HOOK_FAIL_OPEN would invert the default-policy
    # tests. env_extra has the final say.
    for k in ("TAUSIK_SKIP_HOOKS", "TAUSIK_HOOK_FAIL_OPEN"):
        env.pop(k, None)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, _HOOK],
        input='{"tool_name":"Write","tool_input":{}}',
        text=True, encoding="utf-8",
        capture_output=True,
        env=env,
        timeout=10,
    )


def test_active_task_allows(tmp_path):
    proj = _make_tausik_project(tmp_path, with_active_task=True)
    res = _run({}, proj)
    assert res.returncode == 0, res.stderr


def test_no_active_task_blocks(tmp_path):
    proj = _make_tausik_project(tmp_path, with_active_task=False)
    res = _run({}, proj)
    assert res.returncode == 2
    assert "BLOCKED" in res.stderr


def test_no_tausik_dir_allows(tmp_path):
    # No .tausik/ at all → not a tausik project, no enforcement.
    res = _run({}, tmp_path)
    assert res.returncode == 0


def test_fail_secure_is_default_on_db_error(tmp_path):
    """Decision #58 — a corrupt DB blocks instead of silently allowing.

    The gate reaches this branch only when tausik.db exists, so a failing
    SELECT is real breakage. Allowing the write there would make the guard
    indistinguishable from a disabled one.
    """
    proj = _make_tausik_project(tmp_path, with_active_task=False, broken_db=True)
    res = _run({}, proj)
    assert res.returncode == 2
    assert "BLOCKED" in res.stderr
    assert "TAUSIK_HOOK_FAIL_OPEN" in res.stderr, "must name the escape hatch"


def test_fail_open_flag_allows_on_db_error(tmp_path):
    """TAUSIK_HOOK_FAIL_OPEN=1: corrupt DB → allow."""
    proj = _make_tausik_project(tmp_path, with_active_task=False, broken_db=True)
    res = _run({"TAUSIK_HOOK_FAIL_OPEN": "1"}, proj)
    assert res.returncode == 0


def test_skip_hooks_env_short_circuits(tmp_path):
    proj = _make_tausik_project(tmp_path, with_active_task=False)
    res = _run({"TAUSIK_SKIP_HOOKS": "1"}, proj)
    assert res.returncode == 0


def test_no_subprocess_left_in_hook_source():
    """Static check: the hook must no longer rely on `subprocess.run`.

    Catches a regression that would re-introduce the 5s timeout flake
    and kill editor responsiveness on Windows.
    """
    with open(_HOOK, encoding="utf-8") as f:
        src = f.read()
    assert "subprocess.run" not in src, (
        "task_gate.py must use direct sqlite3 SELECT, not subprocess"
    )
