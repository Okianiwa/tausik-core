"""Append-only journal of what an autonomous run actually did.

The point of the loop is that nobody watches it, which means the journal is the
only account of the night. It is written per-iteration rather than at the end so
a crashed supervisor still leaves a record — an entry opened and never closed is
itself the evidence that something died mid-iteration.

One JSON object per line, appended and flushed immediately: a partial last line
is expected and must be tolerated by every reader.
"""

from __future__ import annotations

import json
import os
from typing import Any
from datetime import datetime, timezone

JOURNAL_FILENAME = "autoloop-run.jsonl"

REASON_CRASHED = "crashed"

# Lines carrying this key are events, not iterations: they are written by hooks
# and by the supervisor between iterations, and must not be folded into the
# per-iteration collapse below.
EVENT_KEY = "event"

EVENT_SESSION_EXHAUSTED = "session_budget_exhausted"
EVENT_SESSION_END_BLOCKED = "session_end_blocked"
EVENT_SESSION_CLOSED = "session_closed_during_iteration"
EVENT_HANDOFF = "iteration_handoff"

SESSION_VIOLATIONS = (EVENT_SESSION_END_BLOCKED, EVENT_SESSION_CLOSED)

# Ways an iteration is allowed to end. "soft" and "hard" are what exit_guard
# writes when the context window fills up — the designed exit, not a failure;
# without them here a report calls every recycled iteration a сбой and the real
# failures stop standing out. "context" is the name that shape had before.
NORMAL_EXITS = (None, "context", "completed", "soft", "hard", REASON_CRASHED)


def journal_path(project_dir: str) -> str:
    return os.path.join(str(project_dir), ".tausik", JOURNAL_FILENAME)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append(project_dir: str, entry: dict) -> bool:
    path = journal_path(project_dir)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
    except OSError:
        return False
    return True


def append_event(project_dir: str, event: str, **fields) -> bool:
    """Record something that happened between or inside iterations.

    Events share the file with iteration records because there is only one
    account of the night and splitting it would let the two halves disagree
    about what happened when. They are told apart by the `event` key alone.
    """
    return append(project_dir, {EVENT_KEY: event, "at": _now(), **fields})


def read_events(project_dir: str, event: str | None = None) -> list[dict]:
    """Event lines in write order, optionally of one kind.

    Unlike iterations these are never collapsed: two blocked session_end calls
    are two facts, and merging them would hide the second one.
    """
    events = []
    for entry in _read_lines(project_dir):
        kind = entry.get(EVENT_KEY)
        if not kind:
            continue
        if event is None or kind == event:
            events.append(entry)
    return events


def open_iteration(project_dir: str, iteration: int, task_slug: str, status_before: dict) -> dict:
    """Record the start of an iteration. The returned dict is closed later."""
    entry: dict[str, Any] = {
        "iteration": iteration,
        "task_slug": task_slug,
        "started_at": _now(),
        "ended_at": None,
        "exit_reason": None,
        "percent_at_exit": None,
        "status_before": status_before.get(task_slug),
        "status_after": None,
        "commits": [],
    }
    append(project_dir, entry)
    return entry


def close_iteration(
    project_dir: str,
    entry: dict,
    *,
    exit_reason: str,
    percent_at_exit=None,
    status_after: str | None = None,
    commits: list[str] | None = None,
    cost_usd: float = 0.0,
    tokens: dict | None = None,
) -> dict:
    entry = {
        **entry,
        "ended_at": _now(),
        "exit_reason": exit_reason,
        "percent_at_exit": percent_at_exit,
        "status_after": status_after,
        "commits": commits or [],
        "cost_usd": round(cost_usd, 6),
        "tokens": tokens or {},
    }
    append(project_dir, entry)
    return entry


def _read_lines(project_dir: str) -> list[dict]:
    """Every well-formed JSON object in the file, in write order."""
    path = journal_path(project_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    parsed = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue  # truncated tail after a kill — expected, not an error
        if isinstance(entry, dict):
            parsed.append(entry)
    return parsed


def read_entries(project_dir: str) -> list[dict]:
    """Collapse the append-only log into one entry per iteration.

    A later line for the same iteration supersedes the earlier one, so the
    open-then-close pair reads as a single closed record. An iteration whose
    close never arrived keeps ended_at=None — that is how a crash surfaces.
    """
    by_iteration: dict[int, dict] = {}
    order: list[int] = []
    for entry in _read_lines(project_dir):
        if entry.get(EVENT_KEY):
            continue
        key = entry.get("iteration")
        if not isinstance(key, int):
            continue
        if key not in by_iteration:
            order.append(key)
        by_iteration[key] = entry
    return [by_iteration[key] for key in order]


def mark_orphans_crashed(project_dir: str) -> int:
    """Close entries left open by a dead supervisor. Returns how many.

    Called at the start of a run: an iteration still open when a new run begins
    cannot be alive, so the honest label is "crashed" rather than leaving it
    ambiguous forever.
    """
    orphans = [e for e in read_entries(project_dir) if e.get("ended_at") is None]
    for entry in orphans:
        close_iteration(
            project_dir,
            entry,
            exit_reason=REASON_CRASHED,
            percent_at_exit=entry.get("percent_at_exit"),
            status_after=entry.get("status_before"),
        )
    return len(orphans)


def summarize(project_dir: str) -> dict:
    entries = read_entries(project_dir)
    closed = [e for e in entries if e.get("status_after") == "done"]
    crashed = [e for e in entries if e.get("exit_reason") == REASON_CRASHED]
    failed = [e for e in entries if e.get("exit_reason") not in NORMAL_EXITS]
    return {
        "iterations": len(entries),
        "tasks_done": [e["task_slug"] for e in closed],
        "crashed": crashed,
        "failed": failed,
        "session_violations": [
            e for e in read_events(project_dir) if e.get(EVENT_KEY) in SESSION_VIOLATIONS
        ],
        # Not a violation — a run that stopped for a stated reason. Kept apart
        # so the report can say "the budget ran out" instead of leaving a night
        # that ended early looking like a night that had nothing to do.
        "exhausted": [
            e for e in read_events(project_dir) if e.get(EVENT_KEY) == EVENT_SESSION_EXHAUSTED
        ],
        "cost_usd": round(sum(float(e.get("cost_usd") or 0) for e in entries), 4),
        "tokens": sum_tokens(entries),
        "commits": [c for e in entries for c in (e.get("commits") or [])],
        "entries": entries,
    }


TOKEN_KEYS = ("input", "output", "cache_read", "cache_write", "total")


def sum_tokens(entries: list[dict]) -> dict:
    """Add up per-iteration token counts, tolerating entries written before
    tokens were journalled at all.

    A key nobody ever reported comes back as None, not as 0. The difference is
    the whole point: a run that generated nothing and a run whose iterations
    predate token journalling look identical once both are called zero, and the
    screen would state a measurement it never took.
    """
    totals = dict.fromkeys(TOKEN_KEYS, None)
    for entry in entries:
        tokens = entry.get("tokens")
        if not isinstance(tokens, dict):
            continue
        for key in TOKEN_KEYS:
            value = tokens.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[key] = (totals[key] or 0) + int(value)
    return totals


def work_tokens(tokens: dict) -> int | None:
    """How much work a run did: what it read for the first time plus what it
    wrote. None when neither was ever measured.

    Deliberately not `total`. On a long run `cache_read` is 98% of that sum —
    the same context handed back on every request — so the figure climbs into
    the millions while the window beside it sits at 40%, and the two numbers
    read as a contradiction. Re-reading a cache is not work done.
    """
    if not isinstance(tokens, dict):
        return None
    parts = [tokens.get("cache_write"), tokens.get("output")]
    measured = [int(v) for v in parts if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return sum(measured) if measured else None


def describe_violation(event: dict) -> str:
    """One line about an attempt on the human's session. Never raises."""
    kind = event.get(EVENT_KEY)
    at = event.get("at") or "—"
    task = event.get("task_slug") or "—"
    if kind == EVENT_SESSION_END_BLOCKED:
        return (
            f"{at}, задача {task}: итерация пыталась закрыть сессию "
            f"({event.get('tool') or 'неизвестный вызов'}) — вызов отклонён"
        )
    return (
        f"{at}, задача {task}: сессия #{event.get('session_row') or '—'} закрылась "
        "во время итерации — закрытие просочилось мимо запрета"
    )


def humanize(count: int) -> str:
    """1234567 -> '1.2M'. Exact digits stop being readable past a few thousand."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def format_tokens(count) -> str:
    """A count, or an em dash where there is no measurement — the same answer
    the context percentage already gives for a window it could not read."""
    return "—" if count is None else humanize(count)


def format_report(project_dir: str) -> str:
    summary = summarize(project_dir)
    if not summary["iterations"] and not summary["exhausted"]:
        return "[autoloop] прогонов не было — журнал пуст"
    if not summary["iterations"]:
        # A run that stopped before its first iteration still has to say why.
        # "The journal is empty" for a night that refused to start is the same
        # silence this event exists to break.
        return "\n".join(
            f"[autoloop] итераций не было: у сессии кончилась {e.get('spent') or 'ёмкость'}"
            for e in summary["exhausted"]
        )

    # The place the full breakdown lives. The screens show work done and
    # nothing else; here there is room to say what that number is made of and
    # what the cache re-reads cost on top of it.
    tokens = summary["tokens"]
    lines = [
        f"[autoloop] итераций: {summary['iterations']}, "
        f"работа: {format_tokens(work_tokens(tokens))} "
        f"(вход {format_tokens(tokens['input'])} · выход {format_tokens(tokens['output'])} · "
        f"запись кэша {format_tokens(tokens['cache_write'])} · "
        f"чтение кэша {format_tokens(tokens['cache_read'])} · "
        f"всего {format_tokens(tokens['total'])})",
        f"  стоимость: ${summary['cost_usd']:.4f}",
    ]
    if summary["tasks_done"]:
        lines.append(f"  закрыто задач: {', '.join(summary['tasks_done'])}")
    else:
        lines.append("  закрытых задач нет")
    for entry in summary["failed"]:
        lines.append(
            f"  сбой: итерация {entry['iteration']} ({entry['task_slug']}) — "
            f"{entry.get('exit_reason')}"
        )
    for entry in summary["crashed"]:
        lines.append(
            f"  прервано: итерация {entry['iteration']} ({entry['task_slug']}) — "
            "процесс не закрыл запись, засчитано как падение"
        )
    for event in summary["exhausted"]:
        lines.append(
            f"  прогон остановлен: у сессии кончилась {event.get('spent') or 'ёмкость'} — "
            "задачи не стартовали бы, итерации не тратились"
        )
    for event in summary["session_violations"]:
        lines.append(f"  граница сессии: {describe_violation(event)}")
    if summary["commits"]:
        lines.append(f"  коммиты: {', '.join(summary['commits'])}")
    for entry in summary["entries"]:
        lines.append(
            f"  #{entry['iteration']} {entry['task_slug']}: "
            f"{entry.get('status_before')} → {entry.get('status_after')}, "
            f"контекст на выходе {entry.get('percent_at_exit')}%, "
            f"причина {entry.get('exit_reason')}"
        )
    return "\n".join(lines)
