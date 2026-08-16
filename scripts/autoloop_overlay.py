"""Floating overlay for an autonomous run.

A small always-on-top window rather than another terminal pane. Two reasons:
the cat can actually animate (tkinter has its own clock, while a statusline is
only repainted when the session does something), and the window survives the
run it is watching — you can leave it in a corner and glance at it.

Reads the same `autoloop_tui.collect()` the terminal dashboard reads. One
source of truth: two of them drift, and the one that drifts is the one nobody
runs.

Writes exactly one thing — its own window position — and nothing else. It
cannot stop a run; the kill switch stays a file the human touches.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import autoloop_presence as presence
import autoloop_quips as quips
import autoloop_tui as tui
import autoloop_watch_state as wstate
from tausik_utils import tausik_config_path

REFRESH_MS = 1000
ANIMATE_MS = 450
DEFAULT_SIZE = (330, 156)

# Margins around the content. Everything else about the geometry is measured,
# not declared: see `layout`.
PAD_X, PAD_Y = 12, 12
GUTTER = 10  # between the cat and the metric lines
LINE_GAP = 5  # between metric lines
BLOCK_GAP = 10  # between the metrics block and the quip below it
TASK_MAX_CHARS = 34  # the one line whose length nothing else bounds

# tkinter reports 1x1 for a widget it has not laid out yet.
MIN_MEASURED = 2

BG = "#11131a"
FG = "#e6e9ef"
DIM = "#7b8496"
ACCENT = "#f0b429"
WARN = "#e5534b"
OK = "#57a773"

_STATUS_COLOR = {
    tui.STATUS_RUNNING: ACCENT,
    tui.STATUS_IDLE: DIM,
    tui.STATUS_STOPPED: DIM,
    tui.STATUS_FAILED: WARN,
}

# Weight follows importance, which it did not: the service caption was the
# brightest line and the task slug — the thing anyone opens this window to see —
# the dimmest. One table, so the hierarchy is a fact a test can read.
ROW_FONT = {
    tui.ROLE_TASK: ("Segoe UI", 12, "bold"),
    tui.ROLE_META: ("Segoe UI", 9),
    tui.ROLE_TASKS: ("Consolas", 10),
    tui.ROLE_CONTEXT: ("Consolas", 10),
}
ROW_FG = {
    tui.ROLE_TASK: FG,
    tui.ROLE_META: DIM,
    tui.ROLE_TASKS: FG,
    tui.ROLE_CONTEXT: FG,
}

# The gauge carries the threshold; the colour carries whether to care.
ZONE_COLOR = {
    tui.ZONE_CALM: FG,
    tui.ZONE_WARM: ACCENT,
    tui.ZONE_HOT: WARN,
    tui.ZONE_UNKNOWN: DIM,
}

# The watcher's own line. Two of these are answers to "why is nothing
# happening?" and get the accent; the rest is quiet by design.
PHASE_COLOR = {"arming": ACCENT, "blind": WARN, "busy": DIM}

ROW_ORDER = (tui.ROLE_TASK, tui.ROLE_META, tui.ROLE_TASKS, tui.ROLE_CONTEXT)


def position_from_config(project_dir: str) -> tuple[int, int] | None:
    """Remembered window corner, None when never moved."""
    try:
        with open(tausik_config_path(project_dir), encoding="utf-8") as f:
            raw = json.load(f)
        pos = raw.get("autoloop", {}).get("overlay_position")
    except (OSError, ValueError, AttributeError):
        return None
    if isinstance(pos, list) and len(pos) == 2 and all(isinstance(n, int) for n in pos):
        return (pos[0], pos[1])
    return None


def save_position(project_dir: str, x: int, y: int) -> bool:
    """Persist the corner. Failure is silent — a window that cannot remember
    where it sat is still a working window."""
    path = tausik_config_path(project_dir)
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return False
    if not isinstance(raw, dict):
        return False
    raw.setdefault("autoloop", {})
    if not isinstance(raw["autoloop"], dict):
        return False
    raw["autoloop"]["overlay_position"] = [int(x), int(y)]
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        return False
    return True


def overlay_rows(data: dict) -> list[tuple[str, str]]:
    """(role, text) as painted: `tui.overlay_rows` with the one bound this
    window adds — the task slug is the only line whose length nothing else
    limits, so it is ellipsized here rather than allowed to widen the window.
    """
    return [
        (role, quips.ellipsize(text, TASK_MAX_CHARS) if role == tui.ROLE_TASK else text)
        for role, text in tui.overlay_rows(data)
    ]


# Consecutive "no run" readings before the window goes. Three of them at
# REFRESH_MS is ~3s — long enough that a stumble does not take the window
# down, short enough that nobody wonders why it is still there.
GONE_LIMIT = 3


def run_gone(status, seen_run: bool, misses: int, limit: int = GONE_LIMIT):
    """One reading of the run's status → (close, seen_run, misses).

    A window is a view of a run; once the run is withdrawn the view has no
    subject, and leaving it up spends a process and a corner of the screen on
    announcing that nothing is happening. Decision #13 kept it open "with a
    sleeping cat"; #14 overturned only its clause about modes, and this
    overturns the rest. Closing loses nothing: the run's totals — commits,
    cost, closed tasks — live in the journal and come back with `/auto отчёт`.

    Two guards, both from how the window is actually started and read:

    * `start()` declares the run, raises the window, and only THEN spawns the
      watcher. Status is read through the watcher's state, so the first
      readings can legitimately say "stopped" for a run about to begin.
      Closing on those would mean a window that never opens — so nothing
      closes until the run has been seen alive at least once.
    * A single reading proves little. `refresh` already refuses to die on a
      transient error, and this keeps that promise: it takes `limit` readings
      in a row, not one.

    STATUS_IDLE is a live run between iterations, not an absent one — only
    STATUS_STOPPED counts as gone.
    """
    if status != tui.STATUS_STOPPED:
        return False, True, 0
    if not seen_run:
        return False, seen_run, 0
    misses += 1
    return misses >= limit, seen_run, misses


def overlay_lines(data: dict) -> list[str]:
    """Just the text, in painted order."""
    return [text for _role, text in overlay_rows(data)]


def _corner(screen_w: int, screen_h: int, size=DEFAULT_SIZE) -> tuple[int, int]:
    """Bottom-right, clear of the taskbar."""
    return (screen_w - size[0] - 24, screen_h - size[1] - 72)


# --- geometry from content ------------------------------------------------


def layout(cat, lines, quip, hint):
    """Where every widget sits and how big the window has to be, computed from
    the sizes the widgets themselves ask for. Returns (positions, size).

    The coordinates used to be constants — 26 pixels per metric line, a 330px
    window. tkinter scales the font with the display DPI but not the numbers
    written here, so at 125% the third line slid under the quip and the token
    count was simply not on screen. Nothing was reported: a clipped label
    looks exactly like a label with nothing to say.
    """
    text_x = PAD_X + cat[0] + GUTTER
    positions, y = [], PAD_Y
    for _, line_h in lines:
        positions.append((text_x, y))
        y += line_h + LINE_GAP
    lines_bottom = y - LINE_GAP if lines else PAD_Y

    quip_y = max(PAD_Y + cat[1], lines_bottom) + BLOCK_GAP
    hint_y = quip_y + quip[1] + LINE_GAP
    placement = {
        "cat": (PAD_X, PAD_Y),
        "lines": positions,
        "quip": (PAD_X, quip_y),
        "hint": (text_x, hint_y),
    }
    width = (
        max([text_x + w for w, _ in lines] + [PAD_X + quip[0], text_x + hint[0], PAD_X + cat[0]])
        + PAD_X
    )
    return placement, (width, hint_y + hint[1] + PAD_Y)


def content_layout(cat, lines, quip, hint, fallback=DEFAULT_SIZE):
    """`layout` over live widgets. Returns (None, fallback) when the widgets
    cannot be measured — an unrendered widget reports 1x1, and a window that
    believes that measurement collapses into a dot. A broken root raises
    rather than answers; the window it belongs to must survive that too, so
    the failure is a size, not an exception.
    """
    try:
        sizes = [
            (int(w.winfo_reqwidth()), int(w.winfo_reqheight())) for w in (cat, *lines, quip, hint)
        ]
    except Exception:  # noqa: BLE001 — font metrics vary by backend; an unmeasurable string falls back to the default size
        return None, fallback
    if any(w < MIN_MEASURED or h < MIN_MEASURED for w, h in sizes):
        return None, fallback
    return layout(sizes[0], sizes[1:-2], sizes[-2], sizes[-1])


def apply_size(root, size, corner, current):
    """Resize in place, anchored at the top-left corner: the window grows
    right and down. Returns the size now in effect — the old one when tkinter
    refuses, since a window that kept its size is still readable."""
    if size == current:
        return current
    try:
        root.geometry(f"{size[0]}x{size[1]}+{corner[0]}+{corner[1]}")
    except Exception:  # noqa: BLE001 — a window manager may refuse any geometry; keeping the old one is the graceful answer
        return current
    return size


def run_overlay(project_dir: str, config: dict | None = None) -> int:
    """Open the window. Returns 0 even when it cannot — a missing display is a
    normal situation (ssh, headless CI), not a failure worth a traceback."""
    try:
        import tkinter as tk
    except ImportError:
        print(
            "[autoloop] tkinter недоступен — оверлей не открыть. "
            "Используй терминальный дашборд: autoloop_run.py watch"
        )
        return 0

    try:
        root = tk.Tk()
    except Exception:  # noqa: BLE001 — tkinter raises TclError, but a broken display varies by platform
        print(
            "[autoloop] графическая среда недоступна — оверлей не открыть. "
            "Используй терминальный дашборд: autoloop_run.py watch"
        )
        return 0

    root.overrideredirect(True)
    root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", 0.92)
    except Exception:  # noqa: BLE001 — transparency is a nicety, not a requirement
        pass  # transparency is a nicety, not a requirement
    root.configure(bg=BG)

    width, height = DEFAULT_SIZE
    saved = position_from_config(project_dir)
    x, y = saved or _corner(root.winfo_screenwidth(), root.winfo_screenheight())
    root.geometry(f"{width}x{height}+{x}+{y}")

    frame = tk.Frame(root, bg=BG, highlightbackground="#2a2f3d", highlightthickness=1)
    frame.pack(fill="both", expand=True)

    # Drawn from the start: an empty label measures 4 pixels wide, and the
    # first fit would put the metric lines where the cat is about to be.
    cat = tk.Label(
        frame,
        text=tui.cat_frame(tui.STATUS_IDLE, 0),
        font=("Consolas", 10),
        fg=ACCENT,
        bg=BG,
        justify="left",
    )
    cat.place(x=PAD_X, y=PAD_Y)

    labels = []
    for role in ROW_ORDER:
        label = tk.Label(
            frame,
            text="",
            font=ROW_FONT[role],
            fg=ROW_FG[role],
            bg=BG,
            anchor="w",
            justify="left",
        )
        label.place(x=104, y=16 + len(labels) * 26)
        labels.append(label)

    # One line wide, no wrapping: the text is ellipsized to fit, so a long task
    # slug cannot push the layout around.
    quip = tk.Label(frame, text="", font=("Segoe UI", 9), fg="#9aa4b8", bg=BG, anchor="w")
    quip.place(x=12, y=108)

    hint = tk.Label(frame, text="перетащи · Esc закрыть", font=("Segoe UI", 7), fg="#4a5163", bg=BG)
    hint.place(x=104, y=132)

    # Heterogeneous on purpose — a frame counter, a drag offset, a size and a
    # corner live together because they are one window's mutable state, and
    # the closures below all read and write it. Annotated so the union does
    # not collapse into a type none of the members satisfy.
    state: dict[str, Any] = {
        "tick": 0,
        "drag": None,
        "size": DEFAULT_SIZE,
        "corner": (x, y),
        "seen_run": False,  # the run must be seen alive before its absence counts
        "gone": 0,
    }
    picker = quips.QuipPicker()

    def fit() -> None:
        """Re-fit the window to what it currently paints. Cheap when nothing
        changed: `apply_size` does nothing when the size is the same.

        Deliberately does not force a repaint before measuring. tkinter
        answers winfo_reqwidth from the text it was last configured with, and
        painting first means painting the widgets where they used to be —
        which left a ghost of the previous line behind the new one.
        """
        placement, size = content_layout(cat, labels, quip, hint)
        if placement:
            cat.place(x=placement["cat"][0], y=placement["cat"][1])
            for label, (lx, ly) in zip(labels, placement["lines"]):
                label.place(x=lx, y=ly)
            quip.place(x=placement["quip"][0], y=placement["quip"][1])
            hint.place(x=placement["hint"][0], y=placement["hint"][1])
        state["size"] = apply_size(root, size, state["corner"], state["size"])

    def refresh() -> None:
        try:
            data = tui.collect(project_dir, config)
        except Exception:  # noqa: BLE001 — the window outlives individual iterations; a transient read error must not close it
            # The window outlives individual iterations; a transient read error
            # must dim the numbers, never take the window down with it.
            for label in labels:
                label.config(text="—")
            quip.config(text="")
            fit()
            root.after(REFRESH_MS, refresh)
            return
        close, state["seen_run"], state["gone"] = run_gone(
            data["status"], state["seen_run"], state["gone"]
        )
        if close:
            root.destroy()  # the run it shows is over; so is its reason to exist
            return
        # The watcher's own words when it has any; the cat's when it does not.
        # A joke is honest content for "no run is going" and a poor answer to
        # "why is it showing that number?" — which is what it used to be.
        watcher = wstate.read(project_dir)
        quip.config(
            text=tui.watch_line(watcher, picker.update(data, time.monotonic())),
            fg=PHASE_COLOR.get((watcher or {}).get("phase"), "#9aa4b8"),
        )
        rows = overlay_rows(data)
        for label, (role, text) in zip(labels, rows):
            label.config(text=text)
            if role == tui.ROLE_META:
                label.config(fg=_STATUS_COLOR.get(data["status"], DIM))
            elif role == tui.ROLE_CONTEXT:
                zone = tui.context_zone(
                    data["percent"],
                    data.get("soft_threshold", 30),
                    data.get("hard_threshold", 75),
                )
                label.config(fg=ZONE_COLOR.get(zone, FG))
        fit()
        root.after(REFRESH_MS, refresh)

    def animate() -> None:
        state["tick"] += 1
        try:
            status = tui.collect(project_dir, config)["status"]
        except Exception:  # noqa: BLE001 — an unreadable snapshot reads as idle rather than killing the tray loop
            status = tui.STATUS_IDLE
        cat.config(text=tui.cat_frame(status, state["tick"]), fg=_STATUS_COLOR.get(status, FG))
        root.after(ANIMATE_MS, animate)

    def start_drag(event) -> None:
        state["drag"] = (event.x_root - root.winfo_x(), event.y_root - root.winfo_y())

    def do_drag(event) -> None:
        if not state["drag"]:
            return
        dx, dy = state["drag"]
        state["corner"] = (event.x_root - dx, event.y_root - dy)
        root.geometry(f"+{state['corner'][0]}+{state['corner'][1]}")

    def end_drag(_event) -> None:
        state["drag"] = None
        state["corner"] = (root.winfo_x(), root.winfo_y())
        save_position(project_dir, *state["corner"])

    def close(_event=None) -> None:
        save_position(project_dir, root.winfo_x(), root.winfo_y())
        root.destroy()

    for widget in (frame, cat, hint, quip, *labels):
        widget.bind("<Button-1>", start_drag)
        widget.bind("<B1-Motion>", do_drag)
        widget.bind("<ButtonRelease-1>", end_drag)
        widget.bind("<Button-3>", close)
    root.bind("<Escape>", close)

    animate()  # before the first refresh: the cat's width sets the text column
    refresh()
    # Claimed here rather than on entry: both returns above happen when there is
    # no window, and a lock taken before them would tell every later supervisor
    # that a window it cannot see is already up.
    presence.claim_overlay(project_dir)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        close()
    finally:
        presence.release_overlay(project_dir)
    return 0
