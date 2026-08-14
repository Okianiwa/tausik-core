"""Run journal: the only account of a night nobody watched."""

import json

import autoloop_journal as journal
import autoloop_run as autoloop
from autoloop_journal import (
    REASON_CRASHED,
    close_iteration,
    format_report,
    journal_path,
    mark_orphans_crashed,
    open_iteration,
    read_entries,
    summarize,
)


def test_iteration_is_recorded_with_the_agreed_fields(project_dir):
    entry = open_iteration(str(project_dir), 1, "task-a", {"task-a": "planning"})
    close_iteration(
        str(project_dir),
        entry,
        exit_reason="soft",
        percent_at_exit=31.2,
        status_after="done",
        commits=["abc1234"],
        cost_usd=1.25,
    )

    (record,) = read_entries(str(project_dir))

    assert record["iteration"] == 1
    assert record["task_slug"] == "task-a"
    assert record["status_before"] == "planning"
    assert record["status_after"] == "done"
    assert record["exit_reason"] == "soft"
    assert record["percent_at_exit"] == 31.2
    assert record["commits"] == ["abc1234"]
    assert record["cost_usd"] == 1.25
    assert record["started_at"] and record["ended_at"]


def test_entries_stay_in_iteration_order(project_dir):
    for n in (1, 2, 3):
        entry = open_iteration(str(project_dir), n, f"task-{n}", {})
        close_iteration(str(project_dir), entry, exit_reason="completed")

    assert [e["iteration"] for e in read_entries(str(project_dir))] == [1, 2, 3]


def test_open_entry_stays_open_until_closed(project_dir):
    open_iteration(str(project_dir), 1, "task-a", {})

    (record,) = read_entries(str(project_dir))

    assert record["ended_at"] is None


# --- crash handling -------------------------------------------------------


def test_orphan_from_a_dead_supervisor_is_marked_crashed(project_dir):
    """AC: a killed process leaves an open entry — the next run labels it."""
    open_iteration(str(project_dir), 1, "task-a", {"task-a": "active"})

    assert mark_orphans_crashed(str(project_dir)) == 1

    (record,) = read_entries(str(project_dir))
    assert record["exit_reason"] == REASON_CRASHED
    assert record["ended_at"] is not None


def test_closed_entries_are_left_alone(project_dir):
    entry = open_iteration(str(project_dir), 1, "task-a", {})
    close_iteration(str(project_dir), entry, exit_reason="completed")

    assert mark_orphans_crashed(str(project_dir)) == 0
    assert read_entries(str(project_dir))[0]["exit_reason"] == "completed"


def test_truncated_last_line_is_tolerated(project_dir):
    """A process killed mid-write leaves half a line; readers must not choke."""
    entry = open_iteration(str(project_dir), 1, "task-a", {})
    close_iteration(str(project_dir), entry, exit_reason="completed")
    with open(journal_path(str(project_dir)), "a", encoding="utf-8") as f:
        f.write('{"iteration": 2, "task_slug": "half-writ')

    records = read_entries(str(project_dir))

    assert len(records) == 1
    assert records[0]["iteration"] == 1


def test_missing_journal_reads_as_empty(project_dir):
    assert read_entries(str(project_dir)) == []
    assert mark_orphans_crashed(str(project_dir)) == 0


# --- report ---------------------------------------------------------------


def test_report_on_an_empty_journal_says_so(project_dir, capsys, monkeypatch):
    """AC negative: no runs yet is a message and exit 0, never a traceback."""
    monkeypatch.setattr(autoloop, "PROJECT_DIR", project_dir)

    code = autoloop.main(["report"])

    assert code == 0
    assert "прогонов не было" in capsys.readouterr().out


def test_report_lists_closed_tasks_and_cost(project_dir):
    for n, (slug, status, cost) in enumerate(
        [("task-a", "done", 1.0), ("task-b", "active", 0.5)], start=1
    ):
        entry = open_iteration(str(project_dir), n, slug, {slug: "planning"})
        close_iteration(
            str(project_dir),
            entry,
            exit_reason="completed",
            status_after=status,
            percent_at_exit=30.0,
            cost_usd=cost,
        )

    report = format_report(str(project_dir))

    assert "итераций: 2" in report
    assert "task-a" in report
    assert "$1.5000" in report


def test_report_names_failures_and_crashes(project_dir):
    failed = open_iteration(str(project_dir), 1, "task-a", {})
    close_iteration(
        str(project_dir), failed, exit_reason="timeout", status_after="active"
    )
    open_iteration(str(project_dir), 2, "task-b", {})
    mark_orphans_crashed(str(project_dir))

    report = format_report(str(project_dir))

    assert "сбой" in report and "timeout" in report
    assert "прервано" in report and "task-b" in report


def test_report_reports_no_closed_tasks_honestly(project_dir):
    entry = open_iteration(str(project_dir), 1, "task-a", {})
    close_iteration(
        str(project_dir), entry, exit_reason="completed", status_after="active"
    )

    assert "закрытых задач нет" in format_report(str(project_dir))


def test_summary_counts_commits(project_dir):
    entry = open_iteration(str(project_dir), 1, "task-a", {})
    close_iteration(
        str(project_dir), entry, exit_reason="completed", commits=["aaa1111", "bbb2222"]
    )

    assert summarize(str(project_dir))["commits"] == ["aaa1111", "bbb2222"]


def test_journal_survives_an_unwritable_directory(tmp_path):
    """Journalling must never be the thing that kills a run."""
    assert journal.append(str(tmp_path / "no" / "such"), {"iteration": 1}) in (
        True,
        False,
    )


def test_corrupt_lines_are_skipped_not_fatal(project_dir):
    path = journal_path(str(project_dir))
    import os

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not json}\n")
        f.write(json.dumps({"iteration": 5, "task_slug": "ok", "ended_at": "x"}) + "\n")
        f.write("[]\n")  # valid JSON, wrong shape

    records = read_entries(str(project_dir))

    assert [r["iteration"] for r in records] == [5]


# --- work done, as opposed to cache re-read ---------------------------------


def test_work_is_what_was_written_plus_what_was_generated():
    """The figure the screens show. `total` counts `cache_read` — the same
    context handed back on every request — and on a long run that is 98% of it,
    so the number climbed into the millions beside a window at 40%."""
    tokens = {
        "input": 1_000,
        "output": 4_000,
        "cache_write": 20_000,
        "cache_read": 900_000,
        "total": 925_000,
    }

    assert journal.work_tokens(tokens) == 24_000


def test_an_unmeasured_run_is_a_dash_not_a_zero():
    """AC negative: entries written before tokens were journalled must not be
    reported as a run that produced nothing."""
    assert journal.work_tokens({"input": 500, "total": 500}) is None
    assert journal.work_tokens({"cache_write": None, "output": None}) is None
    assert journal.work_tokens({}) is None
    assert journal.work_tokens(None) is None
    assert journal.format_tokens(journal.work_tokens({})) == "—"


def test_an_empty_journal_measures_nothing(project_dir):
    """AC negative: no entries at all — a dash, not 0 тк, and no arithmetic on
    None along the way."""
    tokens = summarize(str(project_dir))["tokens"]

    assert tokens["total"] is None
    assert journal.work_tokens(tokens) is None
    assert journal.format_tokens(journal.work_tokens(tokens)) == "—"


def test_half_a_measurement_still_counts():
    """One of the two parts missing is not a reason to refuse the other: a run
    that wrote no cache still generated output."""
    assert journal.work_tokens({"output": 700}) == 700
    assert journal.work_tokens({"cache_write": 300}) == 300


def test_real_work_under_a_thousand_is_not_rounded_away():
    """AC negative: `0.0k` for 900 tokens reads as nothing happened."""
    assert journal.format_tokens(900) == "900"
    assert journal.format_tokens(0) == "0"
    assert journal.format_tokens(1_500) == "1.5k"


def test_the_full_breakdown_stays_in_the_report(project_dir):
    """AC: the screens show one number; this is where the rest of them live."""
    entry = open_iteration(str(project_dir), 1, "task-a", {})
    close_iteration(
        str(project_dir),
        entry,
        exit_reason="completed",
        tokens={
            "input": 1_000,
            "output": 4_000,
            "cache_write": 20_000,
            "cache_read": 900_000,
            "total": 925_000,
        },
    )

    report = format_report(str(project_dir))

    assert "работа: 24.0k" in report
    assert "запись кэша 20.0k" in report
    assert "чтение кэша 900.0k" in report
    assert "всего 925.0k" in report
    assert "выход 4.0k" in report
