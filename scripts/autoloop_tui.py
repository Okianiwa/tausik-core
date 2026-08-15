"""Live dashboard for an autonomous run.

Strictly a reader. It opens no database for writing, sends no signals and cannot
stop anything — the kill switch is a file the human touches, and a dashboard
that could also stop the loop would be one more thing to be wrong about at 3am.

Two layers on purpose, and now two files: this one turns files on disk into a
plain dict and formats it, `autoloop_screen` paints it in a terminal and
`autoloop_overlay` in a window. Nothing about the numbers depends on a screen
being attached, which is why the tests can exercise all of it.
"""

from __future__ import annotations

import glob
import json
import os
import time
import sqlite3
from datetime import datetime, timezone

import autoloop_journal as journal
import autoloop_run_state as run_state

# Presentation lives in autoloop_format and is re-exported here under the names
# it has always had: the screen, the overlay and the tests reach it through
# `tui.`, and splitting a file is not a reason to change three call sites.
from autoloop_format import (  # noqa: F401 — re-export: consumers call these via `tui.`
    CAT_BROKEN,
    CAT_IDLE,
    CAT_SLEEPING,
    CAT_WORKING,
    CELL_ACTIVE,
    CELL_DONE,
    CELL_QUEUED,
    MAX_READING_AGE_S,
    MODE_AGENTS,
    MODE_CHAT,
    STATUS_FAILED,
    STATUS_IDLE,
    STATUS_RUNNING,
    STATUS_STOPPED,
    bar,
    caption,
    cat_frame,
    format_cost,
    format_elapsed,
    format_percent,
    format_reading_age,
    progress_bar,
    progress_label,
    render_text,
    work_full,
    work_short,
)

# --- data layer -----------------------------------------------------------


def _run_is_live(project_dir: str) -> bool:
    return run_state.mode(project_dir) is not None


def _newest_state(project_dir: str) -> dict:
    """Freshest per-session reading. Absence is normal, not an error.

    A JSON file in that directory is not automatically a measurement: the loop
    writes generated permission profiles nearby, and one of those, freshly
    rewritten at the start of a run, once outranked every real reading here.
    A reading always carries `percent` (None when the transcript was
    unmeasurable — that is still an answer); anything without the key is
    somebody else's file and is skipped rather than shown as a measurement.
    """
    pattern = os.path.join(project_dir, ".tausik", "autoloop", "*.json")
    newest, newest_mtime = {}, -1.0
    for path in glob.glob(pattern):
        try:
            mtime = os.path.getmtime(path)
            if mtime <= newest_mtime:
                continue
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and "percent" in data:
            newest, newest_mtime = data, mtime
    # Age travels with the reading instead of erasing it. The watcher needs
    # strict freshness — it decides whether to act; a window does not. A
    # reading is written when the agent's turn ends, so during a long turn it
    # ages on purpose, and blanking it there hid the real figure behind an em
    # dash for the whole run. Window fill does not decay: it only grows.
    if newest:
        newest = dict(newest, age_seconds=max(0, int(time.time() - newest_mtime)))
    return newest


def _task_counts(project_dir: str) -> dict:
    """{status: [slug, …]} straight from the DB, read-only."""
    db = os.path.join(project_dir, ".tausik", "tausik.db")
    buckets: dict[str, list[str]] = {}
    if not os.path.exists(db):
        return buckets
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2) as conn:
            rows = conn.execute(
                "SELECT slug, status FROM tasks WHERE archived_at IS NULL"
            ).fetchall()
    except sqlite3.Error:
        return buckets
    for slug, status in rows:
        buckets.setdefault(str(status), []).append(str(slug))
    return buckets


def _elapsed_seconds(entries: list[dict]) -> int:
    starts = [e.get("started_at") for e in entries if e.get("started_at")]
    if not starts:
        return 0
    try:
        first = datetime.fromisoformat(str(starts[0]).replace("Z", "+00:00"))
    except ValueError:
        return 0
    return max(0, int((datetime.now(timezone.utc) - first).total_seconds()))


def collect(project_dir: str, config: dict | None = None) -> dict:
    """Everything the screen needs, as plain data. Never raises."""
    config = config or {}
    entries = journal.read_entries(project_dir)
    state = _newest_state(project_dir)
    tasks = _task_counts(project_dir)
    summary = journal.summarize(project_dir)

    mode = run_state.mode(project_dir)
    live = mode is not None
    chat = mode == MODE_CHAT
    last = entries[-1] if entries else {}
    if live:
        status = STATUS_RUNNING
    elif run_state.stop_requested(project_dir):
        status = STATUS_STOPPED
    elif last.get("exit_reason") in ("crashed", "timeout") or (
        last.get("exit_reason") and last.get("status_after") != "done"
    ):
        status = STATUS_FAILED
    elif entries:
        status = STATUS_IDLE
    else:
        status = STATUS_STOPPED

    queued = tasks.get("planning", [])
    done = tasks.get("done", [])
    active = tasks.get("active", [])
    total = len(queued) + len(done) + len(active) + len(tasks.get("blocked", []))

    # Journal numbers describe the agents-mode run that wrote them, so in chat
    # mode they belong to somebody else's run and are hidden. With no run at
    # all they stay: totals of the last run are a report, and the caption says
    # plainly that nothing is running. The iteration counter is the exception —
    # "итерация 3" beside an idle cat reads as work happening right now.
    from_journal = not chat
    # Same shape, no numbers: an unmeasured count already renders as an em
    # dash, so the window says "not measured" rather than a confident zero.
    unmeasured = dict.fromkeys(("input", "output", "cache_read", "cache_write", "total"))
    # Read the fill now rather than take the last stored reading: that one is
    # written when a turn ends, and a turn can run for an hour while the window
    # keeps filling. The stored reading stays as the fallback, and only it
    # carries an age — a figure read a moment ago needs no caveat.
    now_percent = run_state.live_percent(project_dir) if chat else None
    percent = now_percent if now_percent is not None else state.get("percent")
    reading_age = None if now_percent is not None else state.get("age_seconds")
    return {
        "mode": mode,
        "status": status,
        "caption": caption(status, mode),
        "current_task": (
            state.get("task_slug") if chat else last.get("task_slug") if live else None
        )
        or (active[0] if active else None),
        "iteration": run_state.cleanups_done(project_dir)
        if chat
        else (last.get("iteration") or 0)
        if live
        else 0,
        "percent": percent,
        "reading_age_seconds": reading_age,
        "soft_threshold": config.get("soft_threshold", 30),
        "tokens": summary["tokens"] if from_journal else unmeasured,
        "cost_usd": summary["cost_usd"] if from_journal else None,
        "elapsed_seconds": _elapsed_seconds(entries)
        if from_journal
        else run_state.chat_elapsed(project_dir)
        if chat
        else 0,
        "tasks_done": done,
        "tasks_queued": queued,
        "tasks_active": active,
        "tasks_total": total,
        "commits": summary["commits"] if from_journal else [],
        "direction": run_state.chat_run(project_dir).get("direction") if chat else None,
        "entries": entries,
    }

