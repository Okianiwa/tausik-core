"""Shared state between the in-session hooks and the out-of-process supervisor.

The supervisor cannot see the conversation and the hook cannot outlive the
process, so a file is the only channel between them. It is rewritten on every
Stop and must therefore be cheap and crash-safe: written to a temp file in the
same directory, then os.replace'd (atomic on Windows too).

State is per-session — `.tausik/autoloop/<session_id>.json` — and that is not
tidiness. The first real run had an interactive session and a supervised one
open at once; with a single project-wide file they overwrote each other's
readings, and the supervisor was one step away from deciding an iteration's fate
from a measurement of somebody else's conversation. Every reader must therefore
name the session it means. Tests never caught this because they only ever ran
one session at a time.

DB access here is a direct read-only sqlite connection rather than the CLI.
`_common.py` established that precedent for hooks — a subprocess per Stop event
costs ~300 ms and this runs on every single turn.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from tausik_utils import tausik_config_path  # scripts/ is on the path via the package's parent

STATE_DIRNAME = "autoloop"
PROFILES_DIRNAME = "profiles"
STOP_FILENAME = ".autoloop.stop"
UNKNOWN_SESSION = "unknown-session"

# Session ids are UUIDs in practice; anything else becomes a filename here, so
# strip what a path must not contain rather than trusting the caller. Dots are
# excluded too — keeping them would let "../.." survive as "...." and climb.
_UNSAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9_-]")

STATE_MAX_AGE_SECONDS = 24 * 3600

# Task is "complete" in the sense the loop cares about: nothing is half-written,
# so the process may die without losing work.
STATE_COMPLETE = "complete"
STATE_IN_PROGRESS = "in_progress"
STATE_IDLE = "idle"

DEFAULT_SOFT_THRESHOLD = 30.0
DEFAULT_HARD_THRESHOLD = 75.0


def state_dir(project_dir: str) -> str:
    """Readings only — one file per session, nothing else.

    Generated permission profiles used to land here too. Two things broke at
    once: the dashboard takes the freshest `*.json` as the current reading, and
    a profile is rewritten at the start of every run, so it was always newer
    than any measurement; pruning, meanwhile, deleted the profile as if it were
    a day-old reading. Anything that is not a reading goes in a subdirectory.
    """
    return os.path.join(project_dir, ".tausik", STATE_DIRNAME)


def profiles_dir(project_dir: str) -> str:
    return os.path.join(state_dir(project_dir), PROFILES_DIRNAME)


def safe_session_id(session_id: str | None) -> str:
    """Filename-safe session id; empty input becomes an explicit placeholder.

    A missing id is kept distinguishable rather than silently merged into a
    shared file — merging is exactly the bug this module now guards against.
    """
    cleaned = _UNSAFE_CHARS_RE.sub("", str(session_id or ""))
    return cleaned or UNKNOWN_SESSION


def state_path(project_dir: str, session_id: str | None) -> str:
    return os.path.join(state_dir(project_dir), f"{safe_session_id(session_id)}.json")


def stop_switch_path(project_dir: str) -> str:
    return os.path.join(project_dir, ".tausik", STOP_FILENAME)


def _db_path(project_dir: str) -> str:
    return os.path.join(project_dir, ".tausik", "tausik.db")


def load_config(project_dir: str) -> dict:
    """Read the `autoloop` block of .tausik/config.json, filling defaults.

    A missing or malformed config is not an error — the loop has working
    defaults and must not break the session over a typo in a settings file.
    """
    config: dict = {}
    path = tausik_config_path(project_dir)
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict) and isinstance(raw.get("autoloop"), dict):
            config = raw["autoloop"]
    except (OSError, ValueError):
        config = {}

    def _number(key: str, default: float) -> float:
        value = config.get(key)
        return (
            float(value) if isinstance(value, (int, float)) and value > 0 else default
        )

    return {
        "context_window": int(_number("context_window", 1_000_000)),
        "soft_threshold": _number("soft_threshold", DEFAULT_SOFT_THRESHOLD),
        "hard_threshold": _number("hard_threshold", DEFAULT_HARD_THRESHOLD),
        "max_iterations": int(_number("max_iterations", 20)),
        "max_idle_iterations": int(_number("max_idle_iterations", 3)),
        "max_crash_streak": int(_number("max_crash_streak", 2)),
    }


def read_session_row(project_dir: str) -> tuple[int | None, str | None]:
    """(id, ended_at) of the newest session row; (None, None) when unreadable.

    The supervisor takes this before and after an iteration. A session that was
    open going in and closed coming out is the one thing the guard cannot rule
    out by itself — a hook only sees the calls it matches, and the point of the
    measurement is to catch what the rule missed.
    """
    db = _db_path(project_dir)
    if not os.path.exists(db):
        return None, None
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2) as conn:
            row = conn.execute(
                "SELECT id, ended_at FROM sessions ORDER BY id DESC LIMIT 1"
            ).fetchone()
    except sqlite3.Error:
        return None, None
    return (row[0], row[1]) if row else (None, None)


def read_task_state(project_dir: str) -> tuple[str, str | None]:
    """Return (state, task_slug) for the single active task.

    "complete" means every plan step is ticked off, or the task already left
    the active state — either way the agent is at a natural seam and the
    process can be recycled without stranding half-done work.

    Zero or several active tasks both yield "idle": with two active tasks
    there is no single answer, and guessing one would let the loop close a
    session mid-edit on the other.
    """
    db = _db_path(project_dir)
    if not os.path.exists(db):
        return STATE_IDLE, None
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2) as conn:
            rows = conn.execute(
                "SELECT slug, status, plan FROM tasks WHERE status='active' LIMIT 2"
            ).fetchall()
    except sqlite3.Error:
        return STATE_IDLE, None

    if len(rows) != 1:
        return STATE_IDLE, None

    slug, status, plan_raw = rows[0]
    if status in ("done", "review"):
        return STATE_COMPLETE, slug

    steps = _parse_plan(plan_raw)
    if steps and all(step.get("done") for step in steps):
        return STATE_COMPLETE, slug
    return STATE_IN_PROGRESS, slug


def _parse_plan(plan_raw) -> list[dict]:
    """Plan steps as [{"step": str, "done": bool}], [] when absent or malformed."""
    if not plan_raw:
        return []
    try:
        parsed = json.loads(plan_raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def read_state(project_dir: str, session_id: str | None) -> dict:
    """This session's last reading, {} when absent or malformed.

    Absence is a real answer — it means nobody measured this session — and the
    caller must treat it as such instead of reaching for another session's file.
    """
    try:
        with open(state_path(project_dir, session_id), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(project_dir: str, session_id: str | None, payload: dict) -> bool:
    """Atomically replace this session's state file. False when the write failed."""
    target = state_path(project_dir, session_id)
    tmp = f"{target}.tmp"
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False
    return True


def prune_state_files(
    project_dir: str, max_age_seconds: int = STATE_MAX_AGE_SECONDS
) -> int:
    """Delete state files older than the cutoff. Returns how many went.

    One file per session means the directory grows with every run; without this
    it accumulates forever. Called at the start of a run, never mid-flight — a
    live session's file must not vanish under it.

    Top level only, and files only: the permission profiles under `profiles/`
    are not readings and must survive the cleanup — a run whose profile was
    swept away would fall back to the unrestricted one.
    """
    directory = state_dir(project_dir)
    if not os.path.isdir(directory):
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    for name in os.listdir(directory):
        if not name.endswith(".json"):
            continue
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        try:
            if os.path.getmtime(path) >= cutoff:
                continue
            os.unlink(path)
        except OSError:
            continue
        removed += 1
    return removed
