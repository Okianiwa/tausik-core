"""Whose minutes are these? The mark that separates a run from the person.

An iteration never opens a TAUSIK session of its own — it works inside the one
that is already open, which is the human's. By session id its events are
indistinguishable from theirs, so a night of autonomous work spent the 180
minutes of Rule 9.2 and greeted them in the morning with a session that would
not let them start a task.
"""

from __future__ import annotations

import io
import sqlite3

import activity_event
import pytest


@pytest.fixture
def hooked(project_dir, monkeypatch):
    """The PostToolUse hook, pointed at a throwaway project.

    `events` mirrors the real schema. The hook swallows every error by design,
    so a missing table would show up as "no row written" — a passing test for
    the wrong reason.
    """
    conn = sqlite3.connect(project_dir / ".tausik" / "tausik.db")
    conn.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, action TEXT NOT NULL, "
        "actor TEXT, details TEXT, "
        "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')))"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.delenv("TAUSIK_SKIP_HOOKS", raising=False)
    monkeypatch.delenv("TAUSIK_AUTONOMY", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    return project_dir


def actors_of(project_dir) -> list:
    conn = sqlite3.connect(project_dir / ".tausik" / "tausik.db")
    try:
        return [row[0] for row in conn.execute("SELECT actor FROM events")]
    finally:
        conn.close()


def test_an_unattended_run_marks_its_own_events(hooked, monkeypatch):
    monkeypatch.setattr(activity_event, "_actor", lambda _dir: "autoloop")

    activity_event.main()

    assert actors_of(hooked) == ["autoloop"]


def test_a_human_at_the_keyboard_leaves_no_mark(hooked):
    """Autonomy is off here — no flag, no run marker — so the event is theirs."""
    activity_event.main()

    assert actors_of(hooked) == [None]


def test_autonomy_needs_all_of_its_conditions(hooked, monkeypatch):
    """The flag alone is not autonomy: a TAUSIK_AUTONOMY left in somebody's
    shell must not start deducting their minutes."""
    monkeypatch.setenv("TAUSIK_AUTONOMY", "1")

    assert activity_event._actor(str(hooked)) is None  # no run marker


def test_a_marked_unattended_run_is_recognised(hooked, monkeypatch):
    monkeypatch.setenv("TAUSIK_AUTONOMY", "1")
    (hooked / ".tausik" / ".autoloop.run").write_text("1", encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))  # not a terminal

    assert activity_event._actor(str(hooked)) == "autoloop"


def test_an_unanswerable_question_counts_as_the_human(hooked, monkeypatch):
    """AC negative: autonomy that cannot be determined must not silently erase
    somebody's minutes. Counting a run as theirs costs time; the reverse hides
    real time from the gate that protects them."""

    def exploding(_dir):
        raise RuntimeError("autoloop package missing")

    monkeypatch.setattr("autoloop.autonomy.is_enabled", exploding)

    assert activity_event._actor(str(hooked)) is None


def test_the_hook_never_blocks_a_tool_call(hooked, monkeypatch):
    """Best-effort by contract: a database that will not open is not a reason
    to fail the tool the human just ran."""
    monkeypatch.setattr(activity_event, "_db_path", lambda _dir: "/nowhere/x.db")

    assert activity_event.main() == 0
