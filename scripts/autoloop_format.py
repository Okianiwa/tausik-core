"""How a run LOOKS: the cat, the bars, and every figure turned into text.

Split out of `autoloop_tui`, which had grown to the 400-line gate doing two
jobs. That module still owns the reading of files on disk; everything here is
pure — same input, same string, no clock, no filesystem — which is why the
tests can pin the exact characters.

Imported back into `autoloop_tui` under its old names on purpose: the screen
and the overlay call `tui.progress_bar(...)`, and a rename would have turned a
file split into a change for three consumers. The bar has exactly one author
(convention #12), and this is where it lives now.
"""

from __future__ import annotations

import autoloop_journal as journal
import autoloop_run_state as run_state

MODE_AGENTS = run_state.MODE_AGENTS
MODE_CHAT = run_state.MODE_CHAT

STATUS_RUNNING = "running"
STATUS_IDLE = "idle"
STATUS_STOPPED = "stopped"
STATUS_FAILED = "failed"

# Beyond this, a reading is old enough that the age is worth saying out loud.
MAX_READING_AGE_S = 180

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

_MODE_LABEL = {MODE_CHAT: "в чате", MODE_AGENTS: "агенты"}


def cat_frame(status: str, tick: int) -> str:
    frames = _CAT_BY_STATUS.get(status, CAT_IDLE)
    return frames[tick % len(frames)].strip("\n")


def caption(status: str, mode: str | None = None) -> str:
    """What the run is doing, and — while one is going — which mode it is.

    The two modes report different things (iterations against cleanups, a cost
    ledger against none), so a viewer who cannot tell them apart reads the
    wrong meaning into the same row of numbers.
    """
    text = _CAPTION.get(status, "")
    label = _MODE_LABEL.get(mode or "")
    return f"{label} · {text}" if label and text else text


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


def work_short(data: dict) -> str:
    """The work metric for the window, which has room for exactly one. Chat
    tokens are never measured — the journal describes an agents run — and the
    field kept for them printed `работа — тк`, which reads as a line cut off,
    not as "not measured". Cleanups are what the chat does have."""
    if data.get("mode") == MODE_CHAT:
        return f"уборок {max(0, int(data.get('iteration') or 0))}"
    return f"работа {journal.format_tokens(journal.work_tokens(data['tokens']))} тк"


def work_full(data: dict) -> tuple[str, str, str]:
    """The same metric where a whole line can be spent: label, figure, aside.
    In parts because the two screens paint it differently, and the choice of
    WHAT to show must not be made twice. No aside in chat mode: elapsed time
    already has its own line."""
    if data.get("mode") == MODE_CHAT:
        return ("уборок", str(max(0, int(data.get("iteration") or 0))), "")
    tokens, fmt = data["tokens"], journal.format_tokens
    aside = f"выход {fmt(tokens['output'])} · запись кэша {fmt(tokens['cache_write'])}"
    return "работа", f"{fmt(journal.work_tokens(tokens))} за прогон", aside


def render_text(data: dict) -> str:
    """Plain-text rendering — used by tests and as the fallback when textual
    is unavailable or the output is not a terminal."""
    done, active = len(data["tasks_done"]), len(data["tasks_active"])
    total = data["tasks_total"]
    work_label, work_value, work_aside = work_full(data)
    lines = [
        cat_frame(data["status"], 0),
        f"autoloop · {data['caption']}",
        f"задача:   {data['current_task'] or '—'}",
        f"задачи    {progress_bar(done, active, total)}  {progress_label(done, active, total)}",
        f"контекст  {bar((data['percent'] or 0) / 100)}  {format_percent(data['percent'])}"
        f"{format_reading_age(data.get('reading_age_seconds'))}"
        f"  (порог {int(data['soft_threshold'])}%)",
        f"{work_label:<9} {work_value}" + (f"  ({work_aside})" if work_aside else ""),
        f"время     {format_elapsed(data['elapsed_seconds'])} · {format_cost(data['cost_usd'])}",
    ]
    if data["tasks_queued"]:
        lines.append(f"дальше:   {', '.join(data['tasks_queued'][:4])}")
    if data["commits"]:
        lines.append(f"коммиты:  {', '.join(data['commits'][-4:])}")
    return "\n".join(lines)
