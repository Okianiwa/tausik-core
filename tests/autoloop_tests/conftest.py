"""Fixtures for the autoloop suite.

Kept in its own directory so these fixture names stay out of the way of the
several hundred tests next door — `project_dir` and `transcript` are generic
enough to collide, and a fixture silently shadowed by another conftest is a
bug that reads as a broken test.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

# `pythonpath = ["scripts"]` (pyproject) covers the modules; hooks live one
# level deeper and are imported by name, so they need their own entry.
INSTALL_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = INSTALL_ROOT / "scripts"
HOOKS_DIR = SCRIPTS_DIR / "hooks"
# In the hub the library's own hooks are these hooks; in a project they arrive
# under .tausik-lib/. Tests that assert "the library checkout stays pristine"
# have to look wherever it actually is.
LIB_HOOKS_DIR = HOOKS_DIR
SKILLS_DIR = INSTALL_ROOT / "harness" / "skills"
# The autonomy profile is a project artefact (.claude/settings.autonomy.json);
# here it is the template a project gets handed on bootstrap.
AUTONOMY_PROFILE = INSTALL_ROOT / "harness" / "claude" / "settings.autonomy.json"

sys.path.insert(0, str(HOOKS_DIR))


@pytest.fixture
def project_dir(tmp_path):
    """A throwaway project with a .tausik/ directory and an empty tasks table."""
    tausik = tmp_path / ".tausik"
    tausik.mkdir()
    conn = sqlite3.connect(tausik / "tausik.db")
    # archived_at mirrors the real schema: readers filter on it, and a fixture
    # without the column makes every such query fail into an empty result.
    conn.execute(
        "CREATE TABLE tasks (id INTEGER PRIMARY KEY, slug TEXT, status TEXT, plan TEXT, "
        "archived_at TEXT)"
    )
    # handoff mirrors the real schema: it is the human's note on the session
    # row, and the tests that guard it need somewhere for it to be overwritten.
    conn.execute(
        "CREATE TABLE sessions (id INTEGER PRIMARY KEY, started_at TEXT, ended_at TEXT, "
        "handoff TEXT)"
    )
    conn.execute(
        "INSERT INTO sessions (started_at, ended_at) VALUES ('2026-08-05T10:00:00Z', NULL)"
    )
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def close_session(project_dir):
    """Mark the newest session row as ended."""

    def _close():
        conn = sqlite3.connect(project_dir / ".tausik" / "tausik.db")
        conn.execute("UPDATE sessions SET ended_at = '2026-08-05T12:00:00Z'")
        conn.commit()
        conn.close()

    return _close


@pytest.fixture
def add_task(project_dir):
    """Insert a task row; plan is a list of (step_text, done) pairs."""
    import json

    def _add(slug, status="active", steps=None, plan_raw=None):
        if plan_raw is None:
            plan_raw = (
                json.dumps([{"step": text, "done": done} for text, done in steps])
                if steps is not None
                else None
            )
        conn = sqlite3.connect(project_dir / ".tausik" / "tausik.db")
        conn.execute(
            "INSERT INTO tasks (slug, status, plan) VALUES (?, ?, ?)",
            (slug, status, plan_raw),
        )
        conn.commit()
        conn.close()

    return _add


@pytest.fixture
def transcript(tmp_path):
    """Write a JSONL transcript from a list of dict entries; return its path."""
    import json

    counter = {"n": 0}

    def _write(entries, name=None):
        counter["n"] += 1
        path = tmp_path / (name or f"transcript-{counter['n']}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
        return str(path)

    return _write


def assistant_entry(input_tokens=0, cache_read=0, cache_creation=0, model="claude-opus-5", **extra):
    """A transcript record shaped like a real assistant message with usage."""
    entry = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
                "output_tokens": 100,
            },
        },
    }
    entry.update(extra)
    return entry


@pytest.fixture(autouse=True)
def _session_has_room(monkeypatch):
    """The supervisor asks "is there budget left?" before every iteration, and
    the answer comes from a real CLI subprocess.

    Left alone in a suite that stubs `subprocess.run`, that question is served
    by the stub and counted as an iteration — every brake test saw twice the
    launches it expected. Tests about the budget itself override this.
    """
    try:
        import autoloop_run
    except ImportError:  # a suite that never touches the supervisor
        return
    monkeypatch.setattr(autoloop_run, "session_spent", lambda _dir: None)


class FakeProcess:
    """A process that was never started, and what was done to it afterwards."""

    def __init__(self, cmd):
        self.cmd = cmd
        self.waited = False
        self.killed = False

    def wait(self, *args, **kwargs):
        self.waited = True
        return 0

    def poll(self):
        return None

    def terminate(self):
        self.killed = True

    kill = terminate


@pytest.fixture(autouse=True)
def spawned(monkeypatch):
    """Detached processes a test would have started — recorded, never run.

    Measured, not hypothetical. The supervisor opens the run window with
    `subprocess.Popen`; the loop fixtures stubbed only `subprocess.run`. Three
    suite runs left 58 tkinter windows across the desktop — one per test that
    reached the spawn, every one outliving pytest exactly as designed, because
    the window is built to survive the run it watches.

    Closed here rather than in each fixture: the spawn sits three calls below
    the test, 29 of them reached it, and every new test would inherit the
    escape silently. Tests that care about what was launched read this list;
    the rest are simply kept off the screen.
    """
    import subprocess

    started: list[FakeProcess] = []

    def fake_popen(cmd, *args, **kwargs):
        process = FakeProcess(cmd)
        started.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return started
