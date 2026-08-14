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

# Frames are the cat's whole vocabulary: what it is doing right now.
CAT_WORKING = (
    r"""
 /\_/\
( o.o )
 > ^ <
""",
    r"""
 /\_/\
( -.o )
 > ^ <
""",
    r"""
 /\_/\
( o.- )
 >' '<
""",
)

CAT_IDLE = (
    r"""
 /\_/\
( -.- )
 > ~ <
""",
)

CAT_SLEEPING = (
    r"""
 /\_/\   z
( -.- )  z
 > ~ <  z
""",
    r"""
 /\_/\  z
( -.- ) z
 > ~ <   z
""",
)

CAT_BROKEN = (
    r"""
 /\_/\
( x.x )
 > ! <
""",
)

STATUS_RUNNING = "running"
STATUS_IDLE = "idle"
STATUS_STOPPED = "stopped"
STATUS_FAILED = "failed"

MODE_AGENTS = run_state.MODE_AGENTS
MODE_CHAT = run_state.MODE_CHAT

MAX_READING_AGE_S = 180

_CAT_BY_STATUS = {
    STATUS_RUNNING: CAT_WORKING,
    STATUS_IDLE: CAT_IDLE,
    STATUS_STOPPED: CAT_SLEEPING,
    STATUS_FAILED: CAT_BROKEN,
}

_CAPTION = {
    STATUS_RUNNING: "работаю",
    STATUS_IDLE: "жду",
    STATUS_STOPPED: "прогон не запущен",
    STATUS_FAILED: "прогон прерван",
}


def cat_frame(status: str, tick: int) -> str:
    frames = _CAT_BY_STATUS.get(status, CAT_IDLE)
    return frames[tick % len(frames)].strip("\n")


_MODE_LABEL = {MODE_CHAT: "в чате", MODE_AGENTS: "агенты"}


def caption(status: str, mode: str | None = None) -> str:
    """What the run is doing, and — while one is going — which mode it is.

    The two modes report different things (iterations against cleanups, a cost
    ledger against none), so a viewer who cannot tell them apart reads the
    wrong meaning into the same row of numbers.
    """
    text = _CAPTION.get(status, "")
    label = _MODE_LABEL.get(mode or "")
    return f"{label} · {text}" if label and text else text


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
        "percent": state.get("percent"),
        "reading_age_seconds": state.get("age_seconds"),
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


# --- formatting helpers (pure, so tests can read them) --------------------


# Three states, three glyphs. The middle one exists because "closed" and
# "running" used to share the bar: mid-task the strip was already full of █ and
# read as "everything is done" — the one moment the bar is actually watched.
#
# 100 / 50 / 25 percent of ink, not the denser ▓ (75%): on screen ▓ next to █
# is a seam, not a segment. Three levels have to differ at a glance, at 10pt,
# without counting cells.
CELL_DONE = "█"
CELL_ACTIVE = "▒"
CELL_QUEUED = "░"


def bar(fraction: float | None, width: int = 16) -> str:
    """A single proportion — the context gauge. Task progress is not a
    proportion (see `progress_bar`)."""
    if fraction is None:
        return "─" * width
    filled = max(0, min(width, round(fraction * width)))
    return "█" * filled + "░" * (width - filled)


def progress_bar(done: int, active: int, total: int, width: int = 16) -> str:
    """Closed / running / queued as one strip of exactly `width` cells.

    Two segments rounded independently can ask for more cells than the bar has,
    and a bar wider than its width shifts everything after it on the line. So
    the cells are spent in priority order: a running task always keeps one (a
    task that is invisible while it runs is the defect being fixed), and the
    closed part gives way first.
    """
    if width <= 0:
        return ""
    done, active, total = max(0, int(done)), max(0, int(active)), max(0, int(total))
    if not total:
        return CELL_QUEUED * width
    done = min(done, total)
    active = min(active, total - done)

    done_cells = round(done / total * width) or (1 if done else 0)
    active_cells = round(active / total * width) or (1 if active else 0)
    done_cells -= max(0, done_cells + active_cells - width)
    return (
        CELL_DONE * done_cells
        + CELL_ACTIVE * active_cells
        + CELL_QUEUED * (width - done_cells - active_cells)
    )


def progress_label(done: int, active: int, total: int) -> str:
    """`12/13 · 1 в работе`. The suffix appears only while something runs, so a
    finished queue still reads as a plain N/N and nothing has to be discounted
    mentally."""
    done, active, total = max(0, int(done)), max(0, int(active)), max(0, int(total))
    label = f"{done}/{total}"
    return f"{label} · {active} в работе" if active else label


def format_percent(percent) -> str:
    return "—" if percent is None else f"{percent}%"


def format_elapsed(seconds: int) -> str:
    minutes, sec = divmod(max(0, seconds or 0), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}ч {minutes:02d}м" if hours else f"{minutes}м {sec:02d}с"


def format_reading_age(seconds: int | None) -> str:
    """How old the context reading is, said only when that matters.

    Fresh readings need no caveat; an ageing one is still the truth about a
    window that only grows, so it is shown with its age rather than withheld.
    """
    if seconds is None or seconds <= MAX_READING_AGE_S:
        return ""
    minutes = seconds // 60
    return f" ({minutes} мин назад)" if minutes else f" ({seconds} с назад)"


def format_cost(value: float | None) -> str:
    """Cost is journal-only: the in-chat run keeps no per-iteration ledger, so
    there the honest answer is an em dash, not $0.00."""
    return "—" if value is None else f"${value:.2f}"


def render_text(data: dict) -> str:
    """Plain-text rendering — used by tests and as the fallback when textual
    is unavailable or the output is not a terminal."""
    tokens = data["tokens"]
    done, active = len(data["tasks_done"]), len(data["tasks_active"])
    total = data["tasks_total"]
    lines = [
        cat_frame(data["status"], 0),
        f"autoloop · {data['caption']}",
        f"задача:   {data['current_task'] or '—'}",
        f"задачи    {progress_bar(done, active, total)}  {progress_label(done, active, total)}",
        f"контекст  {bar((data['percent'] or 0) / 100)}  {format_percent(data['percent'])}"
        f"{format_reading_age(data.get('reading_age_seconds'))}"
        f"  (порог {int(data['soft_threshold'])}%)",
        f"работа    {journal.format_tokens(journal.work_tokens(tokens))} за прогон"
        f"  (выход {journal.format_tokens(tokens['output'])}"
        f" · запись кэша {journal.format_tokens(tokens['cache_write'])})",
        f"время     {format_elapsed(data['elapsed_seconds'])} · {format_cost(data['cost_usd'])}",
    ]
    if data["tasks_queued"]:
        lines.append(f"дальше:   {', '.join(data['tasks_queued'][:4])}")
    if data["commits"]:
        lines.append(f"коммиты:  {', '.join(data['commits'][-4:])}")
    return "\n".join(lines)
