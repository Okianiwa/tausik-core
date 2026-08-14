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

import autoloop_quips as quips
import autoloop_tui as tui
from autoloop_journal import format_tokens, work_tokens
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


def overlay_lines(data: dict) -> list[str]:
    """The three text lines beside the cat. Pure, so tests can read them.

    The two numbers on the last line answer different questions and have to
    say so: `ctx` is how full the window is right now, `работа` is what the run
    has produced since it started. Unlabelled and side by side they read as one
    contradictory measurement — 40% next to millions of tokens.
    """
    tokens = data["tokens"]
    done, active = len(data["tasks_done"]), len(data["tasks_active"])
    total = data["tasks_total"]
    return [
        f"autoloop · {data['caption']}",
        quips.ellipsize(data["current_task"] or "—", TASK_MAX_CHARS),
        f"{tui.progress_bar(done, active, total, 10)}"
        f" {tui.progress_label(done, active, total)}"
        f"   ctx {tui.format_percent(data['percent'])}"
        f"   работа {format_tokens(work_tokens(tokens))} тк",
    ]


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
    for index, (font, colour) in enumerate([("Segoe UI", FG), ("Consolas", DIM), ("Consolas", FG)]):
        size = 10 if index else 11
        label = tk.Label(
            frame,
            text="",
            font=(font, size),
            fg=colour,
            bg=BG,
            anchor="w",
            justify="left",
        )
        label.place(x=104, y=16 + index * 26)
        labels.append(label)

    # One line wide, no wrapping: the text is ellipsized to fit, so a long task
    # slug cannot push the layout around.
    quip = tk.Label(frame, text="", font=("Segoe UI", 9), fg="#9aa4b8", bg=BG, anchor="w")
    quip.place(x=12, y=108)

    hint = tk.Label(frame, text="перетащи · Esc закрыть", font=("Segoe UI", 7), fg="#4a5163", bg=BG)
    hint.place(x=104, y=132)

    state = {"tick": 0, "drag": None, "size": DEFAULT_SIZE, "corner": (x, y)}
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
        quip.config(text=picker.update(data, time.monotonic()))
        lines = overlay_lines(data)
        for label, text in zip(labels, lines):
            label.config(text=text)
        labels[0].config(fg=_STATUS_COLOR.get(data["status"], FG))
        if data["percent"] is not None and data["percent"] >= data.get("soft_threshold", 30):
            labels[2].config(fg=ACCENT)
        else:
            labels[2].config(fg=FG)
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
    try:
        root.mainloop()
    except KeyboardInterrupt:
        close()
    return 0
