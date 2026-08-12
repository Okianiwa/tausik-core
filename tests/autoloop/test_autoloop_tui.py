"""Dashboard data layer — the numbers, without a terminal attached."""

import glob
import json
import os
from pathlib import Path

import pytest

import autoloop_journal as journal
import autoloop_tui as tui
from autoloop.state import write_state
from autoloop_git import build_profile
from autoloop_journal import humanize, sum_tokens
from autoloop_tui import (
    STATUS_FAILED,
    STATUS_IDLE,
    STATUS_RUNNING,
    STATUS_STOPPED,
    bar,
    cat_frame,
    collect,
    format_elapsed,
    format_percent,
    progress_bar,
    progress_label,
    render_text,
)

SESSION = "sess-1"


def make_entry(project_dir, iteration=1, slug="task-a", **close):
    entry = journal.open_iteration(
        str(project_dir), iteration, slug, {slug: "planning"}
    )
    return journal.close_iteration(
        str(project_dir), entry, **{"exit_reason": "completed", **close}
    )


def mark_running(project_dir):
    (project_dir / ".tausik" / ".autoloop.run").write_text("123", encoding="utf-8")


# --- status ---------------------------------------------------------------


def test_no_journal_and_no_run_reads_as_not_started(project_dir):
    """AC negative: an idle project opens the dashboard, it does not crash it."""
    data = collect(str(project_dir))

    assert data["status"] == STATUS_STOPPED
    assert data["caption"] == "прогон не запущен"
    assert data["percent"] is None
    assert data["tokens"]["total"] == 0


def test_run_marker_means_running(project_dir):
    mark_running(project_dir)
    make_entry(project_dir)

    assert collect(str(project_dir))["status"] == STATUS_RUNNING


def test_finished_run_reads_as_idle(project_dir, add_task):
    add_task("task-a", status="done", steps=[("a", True)])
    make_entry(project_dir, status_after="done")

    assert collect(str(project_dir))["status"] == STATUS_IDLE


def test_crashed_iteration_reads_as_failed(project_dir):
    make_entry(project_dir, exit_reason="crashed", status_after="active")

    data = collect(str(project_dir))

    assert data["status"] == STATUS_FAILED
    assert data["caption"] == "прогон прерван"


def test_stop_switch_reads_as_stopped(project_dir):
    make_entry(project_dir, status_after="done")
    (project_dir / ".tausik" / ".autoloop.stop").write_text("", encoding="utf-8")

    assert collect(str(project_dir))["status"] == STATUS_STOPPED


# --- numbers --------------------------------------------------------------


def test_context_comes_from_the_freshest_session_file(project_dir):
    write_state(project_dir, SESSION, {"percent": 42.0})

    assert collect(str(project_dir))["percent"] == 42.0


def test_a_foreign_json_next_to_a_reading_is_not_read_as_one(project_dir):
    """AC negative: the defect itself — a non-reading JSON in the readings
    directory, newer than the real measurement, must not become the reading."""
    write_state(project_dir, SESSION, {"percent": 42.0})
    intruder = project_dir / ".tausik" / "autoloop" / "settings.git-off.json"
    intruder.write_text(
        json.dumps({"permissions": {"allow": [], "deny": []}}), encoding="utf-8"
    )
    reading = project_dir / ".tausik" / "autoloop" / f"{SESSION}.json"
    older = os.path.getmtime(intruder) - 60
    os.utime(reading, (older, older))  # profile is the freshest file on disk

    assert collect(str(project_dir))["percent"] == 42.0


def test_generated_profile_is_not_stored_among_the_readings(project_dir):
    """The other half of the fix: the profile is not there to be mistaken."""
    (project_dir / ".claude").mkdir(exist_ok=True)
    (project_dir / ".claude" / "settings.autonomy.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(git push:*)"], "deny": []}}),
        encoding="utf-8",
    )

    path = Path(build_profile(str(project_dir), "off"))

    assert path.exists()
    assert path.parent.name == "profiles"
    assert path.parent.parent == project_dir / ".tausik" / "autoloop"
    assert not glob.glob(str(project_dir / ".tausik" / "autoloop" / "*.json"))


def test_unmeasurable_reading_still_counts_as_one(project_dir):
    """`percent: null` is a measurement that failed, not a foreign file — it
    must reach the dashboard so a stale number cannot outlive it."""
    write_state(project_dir, SESSION, {"percent": None, "reason": "no_usage_found"})

    assert collect(str(project_dir))["percent"] is None


def test_corrupt_state_file_shows_dashes_not_a_traceback(project_dir):
    """AC negative: broken state degrades the reading only."""
    state_dir = project_dir / ".tausik" / "autoloop"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "broken.json").write_text("{not json", encoding="utf-8")

    data = collect(str(project_dir))

    assert data["percent"] is None
    assert format_percent(data["percent"]) == "—"


def test_tokens_are_summed_across_iterations(project_dir):
    make_entry(
        project_dir,
        1,
        tokens={"input": 10, "output": 5, "cache_read": 100, "total": 115},
    )
    make_entry(
        project_dir,
        2,
        tokens={"input": 20, "output": 5, "cache_read": 200, "total": 225},
    )

    tokens = collect(str(project_dir))["tokens"]

    assert tokens["input"] == 30
    assert tokens["output"] == 10
    assert tokens["cache_read"] == 300
    assert tokens["total"] == 340


def test_entries_without_tokens_do_not_break_the_sum(project_dir):
    """Journal lines written before tokens were recorded must stay readable."""
    make_entry(project_dir, 1)
    make_entry(project_dir, 2, tokens={"input": 7, "total": 7})

    assert sum_tokens(journal.read_entries(str(project_dir)))["input"] == 7


def test_tokens_are_read_from_a_real_cli_payload():
    """Shape taken verbatim from a live `claude -p --output-format json` run."""
    import autoloop_run

    payload = {
        "is_error": False,
        "total_cost_usd": 0.0343909,
        "usage": {
            "input_tokens": 10,
            "cache_creation_input_tokens": 16049,
            "cache_read_input_tokens": 20629,
            "output_tokens": 44,
            "service_tier": "standard",
        },
    }

    tokens = autoloop_run.extract_tokens(payload)

    assert tokens["input"] == 10
    assert tokens["output"] == 44
    assert tokens["cache_read"] == 20629
    assert tokens["cache_write"] == 16049
    assert tokens["total"] == 36732


@pytest.mark.parametrize("payload", [{}, {"usage": None}, {"usage": "nope"}])
def test_tokens_missing_from_payload_are_not_invented(payload):
    """Counts are reported, never derived from cost — absent means empty."""
    import autoloop_run

    assert autoloop_run.extract_tokens(payload) == {}


def test_task_progress_counts_from_the_database(project_dir, add_task):
    add_task("done-1", status="done", steps=[("a", True)])
    add_task("done-2", status="done", steps=[("a", True)])
    add_task("next-1", status="planning", steps=[("a", False)])
    add_task("busy-1", status="active", steps=[("a", False)])

    data = collect(str(project_dir))

    assert sorted(data["tasks_done"]) == ["done-1", "done-2"]
    assert data["tasks_queued"] == ["next-1"]
    assert data["tasks_active"] == ["busy-1"]
    assert data["tasks_total"] == 4


def test_commits_are_carried_through(project_dir):
    make_entry(project_dir, commits=["abc1234"])

    assert collect(str(project_dir))["commits"] == ["abc1234"]


def test_missing_database_does_not_crash_collection(tmp_path):
    (tmp_path / ".tausik").mkdir()

    data = collect(str(tmp_path))

    assert data["tasks_total"] == 0
    assert data["status"] == STATUS_STOPPED


# --- formatting -----------------------------------------------------------


@pytest.mark.parametrize(
    "fraction,expected",
    [(0.0, "░" * 16), (1.0, "█" * 16), (0.5, "█" * 8 + "░" * 8)],
)
def test_bar_fills_proportionally(fraction, expected):
    assert bar(fraction) == expected


def test_bar_without_a_value_is_neutral():
    assert set(bar(None)) == {"─"}


def test_bar_clamps_out_of_range_values():
    assert bar(5.0) == "█" * 16
    assert bar(-1.0) == "░" * 16


# --- progress: closed, running, queued ------------------------------------


def test_progress_bar_shows_three_states_at_once():
    """AC: the running task is its own segment — not a gap, not a closed cell."""
    strip = progress_bar(done=6, active=1, total=13, width=10)

    assert set(strip) == {tui.CELL_DONE, tui.CELL_ACTIVE, tui.CELL_QUEUED}
    assert strip == tui.CELL_DONE * 5 + tui.CELL_ACTIVE + tui.CELL_QUEUED * 4


def test_progress_bar_keeps_a_cell_for_a_task_too_small_to_round():
    """One task in a hundred rounds to nothing; drawing nothing is the defect."""
    strip = progress_bar(done=50, active=1, total=100, width=10)

    assert strip.count(tui.CELL_ACTIVE) == 1


def test_a_full_bar_still_means_everything_is_done():
    """AC: with nothing running, N/N stays honest — no phantom active cell."""
    strip = progress_bar(done=13, active=0, total=13, width=10)

    assert strip == tui.CELL_DONE * 10
    assert progress_label(13, 0, 13) == "13/13"


def test_the_counter_does_not_pass_a_running_task_off_as_done():
    """AC: 12 closed + 1 running out of 13 must not read as 13 closed."""
    label = progress_label(done=12, active=1, total=13)

    assert label.startswith("12/13")
    assert "1 в работе" in label


def test_an_empty_queue_neither_divides_by_zero_nor_invents_work():
    """AC negative: 0 tasks — the state right after a finished run."""
    strip = progress_bar(done=0, active=0, total=0, width=10)

    assert strip == tui.CELL_QUEUED * 10
    assert tui.CELL_ACTIVE not in strip
    assert progress_label(0, 0, 0) == "0/0"


@pytest.mark.parametrize("width", [1, 3, 10, 16])
def test_segments_never_outgrow_a_narrow_bar(width):
    """AC negative: more running tasks than cells must not lengthen the line —
    everything printed after the bar would shift with it."""
    strip = progress_bar(done=1, active=8, total=9, width=width)

    assert len(strip) == width
    assert tui.CELL_ACTIVE in strip  # what is running survives the squeeze


def test_dashboard_and_overlay_share_one_bar():
    """AC: one implementation. Two of them drift, and the one that drifts is
    the one nobody happens to be looking at."""
    import inspect

    import autoloop_overlay as overlay
    import autoloop_screen as screen

    for module in (screen, overlay):
        source = inspect.getsource(module)
        assert "tui.progress_bar(" in source
        assert "def progress_bar" not in source
        for glyph in (tui.CELL_DONE, tui.CELL_ACTIVE, tui.CELL_QUEUED):
            assert glyph not in source, f"{module.__name__} не рисует полосу сам"


def test_the_terminal_screen_marks_the_running_task(project_dir, add_task):
    """AC: the textual body, not only the plain-text fallback."""
    import autoloop_screen as screen

    add_task("task-a", status="active", steps=[("a", False)])
    add_task("task-b", status="done", steps=[("a", True)])

    markup = screen.body_markup(collect(str(project_dir), {"soft_threshold": 30}))

    assert "1/2 · 1 в работе" in markup
    assert tui.CELL_ACTIVE in markup


@pytest.mark.parametrize(
    "count,expected", [(0, "0"), (999, "999"), (1_500, "1.5k"), (2_400_000, "2.4M")]
)
def test_humanize_token_counts(count, expected):
    assert humanize(count) == expected


@pytest.mark.parametrize(
    "seconds,expected", [(0, "0м 00с"), (95, "1м 35с"), (7300, "2ч 01м")]
)
def test_format_elapsed(seconds, expected):
    assert format_elapsed(seconds) == expected


def test_cat_has_several_frames_and_cycles():
    frames = {cat_frame(STATUS_RUNNING, n) for n in range(6)}

    assert len(frames) >= 3
    assert cat_frame(STATUS_RUNNING, 0) == cat_frame(STATUS_RUNNING, 3)


def test_each_status_has_its_own_cat():
    faces = {
        status: cat_frame(status, 0)
        for status in (STATUS_RUNNING, STATUS_IDLE, STATUS_STOPPED, STATUS_FAILED)
    }

    assert len(set(faces.values())) == 4
    assert "x.x" in faces[STATUS_FAILED]


def test_text_rendering_holds_the_essentials(project_dir, add_task):
    add_task("task-a", status="active", steps=[("a", False)])
    mark_running(project_dir)
    make_entry(
        project_dir,
        tokens={"input": 1_000, "output": 500, "cache_read": 20_000, "total": 21_500},
    )
    write_state(project_dir, SESSION, {"percent": 31.0})

    text = render_text(collect(str(project_dir), {"soft_threshold": 30}))

    assert "task-a" in text
    assert "31.0%" in text
    assert "21.5k" in text
    assert "порог 30%" in text


def test_text_rendering_marks_the_running_task(project_dir, add_task):
    """AC: the dashboard itself, not just the helper, stops reading as done."""
    add_task("task-a", status="active", steps=[("a", False)])
    add_task("task-b", status="done", steps=[("a", True)])

    text = render_text(collect(str(project_dir)))

    assert "1/2 · 1 в работе" in text
    assert tui.CELL_ACTIVE in text


def test_dashboard_is_read_only(project_dir):
    """AC: the dashboard observes a run; it must not alter or end one.

    Checked against the source because the guarantee is about what the module
    can do at all, not about what one code path happened to do this run.
    Reading the stop file is fine — creating it is not, and neither is any
    other write path.
    """
    import inspect

    import autoloop_screen as screen

    # Both halves of the dashboard: the guarantee followed the code when the
    # screen moved to its own module, it did not stay behind with the reader.
    for module in (tui, screen):
        source = inspect.getsource(module)
        for forbidden in (
            "write_state(",
            "os.unlink",
            "os.remove",
            "shutil.",
            "subprocess",
            "os.kill",
            '"w"',
            "'w'",
        ):
            assert forbidden not in source, (
                f"{module.__name__} не должен использовать {forbidden}"
            )

    # sqlite is opened read-only, so even a stray UPDATE could not land.
    assert "mode=ro" in inspect.getsource(tui)


def test_run_dashboard_without_a_terminal_prints_once(project_dir, capsys, monkeypatch):
    """AC negative: piping `watch` to a file renders text instead of exploding."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    import sys

    class FakeStdout:
        def isatty(self):
            return False

        def write(self, *_args):
            pass

    import autoloop_screen as screen

    monkeypatch.setattr(sys, "stdout", sys.stdout)  # keep capsys intact
    monkeypatch.setattr(tui, "collect", lambda *a, **k: collect(str(project_dir)))
    monkeypatch.setattr("sys.stdout.isatty", lambda: False, raising=False)

    assert screen.run_dashboard(str(project_dir)) == 0
    assert "autoloop" in capsys.readouterr().out
