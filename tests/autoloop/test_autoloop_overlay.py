"""Overlay: the data it paints and the ways it refuses to die."""

import builtins
import json

import pytest

import autoloop_overlay as overlay
import autoloop_tui as tui
from autoloop.state import write_state
from autoloop_overlay import (
    overlay_lines,
    position_from_config,
    run_overlay,
    save_position,
)

SESSION = "sess-1"


def make_data(project_dir, **over):
    data = tui.collect(str(project_dir), {"soft_threshold": 30})
    data.update(over)
    return data


# --- painted content ------------------------------------------------------


def test_lines_show_task_progress_context_and_tokens(project_dir, add_task):
    add_task("busy", status="active", steps=[("a", False)])
    add_task("closed", status="done", steps=[("a", True)])
    write_state(project_dir, SESSION, {"percent": 31.0})
    import autoloop_journal as journal

    entry = journal.open_iteration(str(project_dir), 1, "busy", {})
    journal.close_iteration(
        str(project_dir),
        entry,
        exit_reason="completed",
        tokens={"output": 4_000, "cache_write": 20_000, "cache_read": 60_000, "total": 84_000},
    )

    lines = overlay_lines(make_data(project_dir))

    assert "autoloop" in lines[0]
    assert "busy" in lines[1]
    assert "1/2" in lines[2]
    assert "31.0%" in lines[2]
    # Work done — 20k written plus 4k generated — not the 84k that counts the
    # same context re-read on every request.
    assert "работа 24.0k тк" in lines[2]
    assert "84.0k" not in lines[2]


def test_the_overlay_bar_shows_the_task_it_is_working_on(project_dir, add_task):
    """AC: the same three states reach the window, not only the terminal."""
    add_task("busy", status="active", steps=[("a", False)])
    add_task("closed", status="done", steps=[("a", True)])

    line = overlay_lines(make_data(project_dir))[2]

    assert tui.CELL_ACTIVE in line
    assert "1/2 · 1 в работе" in line


def test_lines_survive_an_empty_project(project_dir):
    lines = overlay_lines(make_data(project_dir))

    assert lines[1] == "—"
    assert "—" in lines[2]  # unmeasured context renders as a dash


def test_no_tasks_does_not_divide_by_zero(project_dir):
    """An empty queue is the state right after a finished run."""
    lines = overlay_lines(make_data(project_dir, tasks_done=[], tasks_total=0))

    assert "0/0" in lines[2]


def test_status_colours_cover_every_state():
    for status in (
        tui.STATUS_RUNNING,
        tui.STATUS_IDLE,
        tui.STATUS_STOPPED,
        tui.STATUS_FAILED,
    ):
        assert status in overlay._STATUS_COLOR


def test_corner_leaves_room_for_the_taskbar():
    x, y = overlay._corner(1920, 1080)

    assert 0 < x < 1920
    assert 0 < y < 1080 - overlay.DEFAULT_SIZE[1]


def test_a_long_task_slug_cannot_stretch_the_window():
    """The one line whose length nothing else bounds."""
    line = overlay_lines(
        {
            "tokens": {"total": 1000},
            "tasks_done": [],
            "tasks_active": [],
            "tasks_total": 0,
            "percent": None,
            "caption": "работаю",
            "current_task": "autoloop-" + "very-long-" * 8,
        }
    )[1]

    assert len(line) <= overlay.TASK_MAX_CHARS
    assert line.endswith("…")


# --- geometry from content ------------------------------------------------
#
# Sizes here are what tkinter would report, not what it does report: the whole
# point is to check the arithmetic without a display. `scale` is the display
# scaling — tkinter grows a font with the DPI, which is exactly what used to
# push the token count out of a 330px window.

CONSOLAS_PX = 7.5  # advance width of Consolas 10pt at 96 dpi
LINE_PX = 19  # its line height

METRICS_LINE = (
    f"{tui.progress_bar(6, 1, 13, 10)} {tui.progress_label(6, 1, 13)}   ctx 24.0%   1.24M тк"
)


class FakeWidget:
    """Knows only how big it wants to be. Opens nothing."""

    def __init__(self, size):
        self._size = size

    def winfo_reqwidth(self):
        return self._size[0]

    def winfo_reqheight(self):
        return self._size[1]


class BrokenWidget:
    """A destroyed widget: tkinter answers questions about it with TclError."""

    def winfo_reqwidth(self):
        raise RuntimeError("invalid command name .!frame.!label")

    winfo_reqheight = winfo_reqwidth


class FakeRoot:
    def __init__(self, refuses=False):
        self.geometries = []
        self.refuses = refuses

    def geometry(self, spec):
        if self.refuses:
            raise RuntimeError("bad window path name")
        self.geometries.append(spec)


def text_size(text, scale=1.0):
    return (round(len(text) * CONSOLAS_PX * scale), round(LINE_PX * scale))


def screen(scale=1.0):
    """Cat, three metric lines, quip and hint as they render at `scale`."""
    return {
        "cat": (round(70 * scale), round(3 * LINE_PX * scale)),
        "lines": [
            text_size("autoloop · работаю", scale),
            text_size("autoloop-overlay-fit", scale),
            text_size(METRICS_LINE, scale),
        ],
        "quip": text_size("считаю токены, не отвлекай", scale),
        "hint": text_size("перетащи · Esc закрыть", scale),
    }


@pytest.mark.parametrize("scale", [1.0, 1.25])
def test_metrics_line_fits_the_window_at_every_screen_scale(scale):
    """AC: seven digits of tokens at 125% is the case the constant lost."""
    parts = screen(scale)

    placement, (width, height) = overlay.layout(**parts)

    metrics_x, metrics_y = placement["lines"][2]
    metrics_w, metrics_h = parts["lines"][2]
    assert metrics_x + metrics_w <= width
    assert metrics_y + metrics_h <= height
    # …and the size it replaced could not have held it.
    assert metrics_x + metrics_w > overlay.DEFAULT_SIZE[0]


def test_layout_keeps_the_cat_left_and_the_lines_beside_it():
    parts = screen()

    placement, (width, height) = overlay.layout(**parts)

    cat_x, cat_y = placement["cat"]
    xs = [x for x, _ in placement["lines"]]
    ys = [y for _, y in placement["lines"]]
    assert xs == sorted(xs) and len(set(xs)) == 1  # one column, right of the cat
    assert all(x > cat_x + parts["cat"][0] for x in xs)
    assert ys == sorted(ys) and len(set(ys)) == 3  # three lines, top to bottom
    assert placement["quip"][1] > ys[-1]  # the reply sits under the metrics
    assert placement["hint"][1] > placement["quip"][1]
    assert placement["quip"][0] == cat_x  # both start at the left margin
    assert placement["hint"][1] + parts["hint"][1] <= height
    assert width > overlay.DEFAULT_SIZE[0]


def test_window_grows_with_its_content():
    """AC: the size follows the text, it is not declared once."""
    small = overlay.layout(**screen())[1]
    grown = overlay.layout(**screen(1.25))[1]

    assert grown[0] > small[0]
    assert grown[1] > small[1]


def test_content_layout_measures_live_widgets():
    parts = screen()
    widgets = {
        "cat": FakeWidget(parts["cat"]),
        "lines": [FakeWidget(size) for size in parts["lines"]],
        "quip": FakeWidget(parts["quip"]),
        "hint": FakeWidget(parts["hint"]),
    }

    placement, size = overlay.content_layout(**widgets)

    assert placement is not None
    assert size == overlay.layout(**parts)[1]


# --- resizing without moving ----------------------------------------------


def test_resizing_pins_the_top_left_corner():
    """AC: the window grows right and down. A window that re-centres itself
    every time a digit is added walks off the screen."""
    root, corner = FakeRoot(), (3709, 557)

    size = overlay.apply_size(root, (420, 190), corner, overlay.DEFAULT_SIZE)
    overlay.apply_size(root, (486, 190), corner, size)

    assert size == (420, 190)
    assert root.geometries == ["420x190+3709+557", "486x190+3709+557"]


def test_an_unchanged_size_touches_nothing():
    root = FakeRoot()

    assert overlay.apply_size(root, (420, 190), (10, 20), (420, 190)) == (420, 190)
    assert root.geometries == []


# --- refusing to die ------------------------------------------------------


def test_unrendered_widgets_do_not_collapse_the_window():
    """AC negative: tkinter reports 1x1 before it has laid a widget out.
    Believing that measurement shrinks the window to a dot."""
    blank = [FakeWidget((1, 1)) for _ in range(6)]

    placement, size = overlay.content_layout(blank[0], blank[1:4], blank[4], blank[5])

    assert placement is None
    assert size == overlay.DEFAULT_SIZE


def test_a_broken_root_yields_a_size_instead_of_an_exception():
    """AC negative: a destroyed widget raises on every question asked of it."""
    placement, size = overlay.content_layout(
        BrokenWidget(), [BrokenWidget()], BrokenWidget(), BrokenWidget()
    )

    assert placement is None
    assert size == overlay.DEFAULT_SIZE


def test_a_root_that_refuses_to_resize_keeps_the_old_size():
    assert overlay.apply_size(FakeRoot(refuses=True), (500, 220), (10, 20), (330, 156)) == (
        330,
        156,
    )


# --- position memory ------------------------------------------------------


def test_position_round_trips(project_dir):
    (project_dir / ".tausik" / "config.json").write_text(
        json.dumps({"autoloop": {"soft_threshold": 30}}), encoding="utf-8"
    )

    assert save_position(str(project_dir), 100, 200) is True
    assert position_from_config(str(project_dir)) == (100, 200)


def test_saving_position_keeps_the_rest_of_the_config(project_dir):
    (project_dir / ".tausik" / "config.json").write_text(
        json.dumps({"autoloop": {"soft_threshold": 45}, "rag": {"mode": "fts5"}}),
        encoding="utf-8",
    )

    save_position(str(project_dir), 10, 20)

    raw = json.loads((project_dir / ".tausik" / "config.json").read_text(encoding="utf-8"))
    assert raw["autoloop"]["soft_threshold"] == 45
    assert raw["rag"]["mode"] == "fts5"


def test_missing_config_means_no_remembered_position(project_dir):
    assert position_from_config(str(project_dir)) is None
    assert save_position(str(project_dir), 1, 2) is False  # nothing to write into


@pytest.mark.parametrize(
    "stored",
    ['{"autoloop": {"overlay_position": "nope"}}', '{"autoloop": {}}', "{broken"],
)
def test_bad_stored_position_is_ignored(project_dir, stored):
    (project_dir / ".tausik" / "config.json").write_text(stored, encoding="utf-8")

    assert position_from_config(str(project_dir)) is None


# --- refusing to die ------------------------------------------------------


def test_missing_tkinter_explains_instead_of_crashing(project_dir, capsys, monkeypatch):
    """AC negative: no GUI is a normal situation — ssh, headless CI."""
    real_import = builtins.__import__

    def no_tkinter(name, *args, **kwargs):
        if name == "tkinter":
            raise ImportError("no tkinter here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_tkinter)

    assert run_overlay(str(project_dir)) == 0
    out = capsys.readouterr().out
    assert "tkinter" in out
    assert "watch" in out  # points at the terminal dashboard instead


def test_broken_display_explains_instead_of_crashing(project_dir, capsys, monkeypatch):
    class FakeTk:
        def __init__(self):
            raise RuntimeError("no display name and no $DISPLAY")

    import tkinter

    monkeypatch.setattr(tkinter, "Tk", FakeTk)

    assert run_overlay(str(project_dir)) == 0
    assert "графическая среда" in capsys.readouterr().out


def test_overlay_never_writes_state_or_stops_the_run():
    """AC: it observes. The only thing it may write is its own position."""
    import inspect

    source = inspect.getsource(overlay)

    for forbidden in (
        "write_state(",
        "autoloop.stop",
        "subprocess",
        "os.kill",
        "os.unlink",
    ):
        assert forbidden not in source, forbidden

    # Exactly one function may open a file for writing, and it is the one that
    # stores the window corner. Checked per-function rather than by counting
    # occurrences in the module: `anchor="w"` on a tkinter label is not a write.
    writers = [
        name
        for name, member in vars(overlay).items()
        if callable(member)
        and getattr(member, "__module__", "") == overlay.__name__
        and "open(" in inspect.getsource(member)
        and '"w"' in inspect.getsource(member)
    ]
    assert writers == ["save_position"], writers


def test_collect_failure_is_handled_by_the_refresh_loop():
    """The window outlives iterations, so a transient read error must not end it."""
    import inspect

    refresh_source = inspect.getsource(overlay.run_overlay)

    assert "except Exception" in refresh_source
    assert refresh_source.count("root.after(REFRESH_MS, refresh)") >= 2
