"""Stop hook: what it records, and the cases where it must stay silent."""

import io
import json

import autoloop_chat_cycle
import pytest
from .conftest import assistant_entry

from autoloop import sensor
from autoloop.state import (
    STATE_COMPLETE,
    STATE_IDLE,
    STATE_IN_PROGRESS,
    load_config,
    read_state,
    read_task_state,
    write_state,
)


SESSION = "test-session"


def run_hook(monkeypatch, project_dir, payload, run="очередь задач"):
    """Invoke the hook the way Claude Code does: JSON on stdin, env for the dir.

    A run is declared first, because measuring is watching: outside one the
    sensor writes nothing at all, and these tests are about what it writes.
    """
    if run:
        autoloop_chat_cycle.start_run(str(project_dir), run)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.delenv("TAUSIK_SKIP_HOOKS", raising=False)
    payload.setdefault("session_id", SESSION)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    return sensor.main()


# --- task completion state ------------------------------------------------


def test_all_steps_done_means_complete(project_dir, add_task):
    add_task("t1", steps=[("a", True), ("b", True)])

    assert read_task_state(project_dir) == (STATE_COMPLETE, "t1")


def test_one_step_left_means_in_progress(project_dir, add_task):
    add_task("t1", steps=[("a", True), ("b", False)])

    assert read_task_state(project_dir) == (STATE_IN_PROGRESS, "t1")


def test_task_without_a_plan_is_in_progress(project_dir, add_task):
    """No plan is no evidence of completion — recycling here would strand work."""
    add_task("t1", plan_raw=None)

    assert read_task_state(project_dir) == (STATE_IN_PROGRESS, "t1")


def test_no_active_task_is_idle(project_dir, add_task):
    add_task("t1", status="done", steps=[("a", True)])

    assert read_task_state(project_dir) == (STATE_IDLE, None)


def test_two_active_tasks_are_idle_not_a_guess(project_dir, add_task):
    """With two active tasks, picking one could close the session mid-edit on the other."""
    add_task("t1", steps=[("a", True)])
    add_task("t2", steps=[("a", False)])

    assert read_task_state(project_dir) == (STATE_IDLE, None)


def test_malformed_plan_json_does_not_crash(project_dir, add_task):
    add_task("t1", plan_raw="{not json")

    assert read_task_state(project_dir) == (STATE_IN_PROGRESS, "t1")


def test_missing_database_is_idle(tmp_path):
    (tmp_path / ".tausik").mkdir()

    assert read_task_state(tmp_path) == (STATE_IDLE, None)


# --- hook behaviour -------------------------------------------------------


def test_writes_state_file_with_measured_fill(monkeypatch, project_dir, add_task, transcript):
    add_task("t1", steps=[("a", True)])
    path = transcript([assistant_entry(input_tokens=2, cache_creation=698, cache_read=299_300)])

    assert run_hook(monkeypatch, project_dir, {"transcript_path": path, "session_id": "s1"}) == 0

    # Read back under the id the payload actually carried: state is per-session,
    # so "s1" and the default SESSION are deliberately different files.
    state = read_state(project_dir, "s1")
    assert state["tokens"] == 300_000
    assert state["percent"] == 30.0
    assert state["task_slug"] == "t1"
    assert state["task_state"] == STATE_COMPLETE
    assert state["model"] == "claude-opus-5"
    assert state["session_id"] == "s1"
    assert state["reason"] is None


def test_unreadable_transcript_records_null_percent(monkeypatch, project_dir, add_task):
    """AC negative: a broken transcript degrades the reading, never the turn."""
    add_task("t1", steps=[("a", False)])

    assert run_hook(monkeypatch, project_dir, {"transcript_path": "nope.jsonl"}) == 0

    state = read_state(project_dir, SESSION)
    assert state["percent"] is None
    assert state["tokens"] is None
    assert state["reason"] == "transcript_unreadable"
    assert state["task_state"] == STATE_IN_PROGRESS


def test_skip_hooks_writes_nothing(monkeypatch, project_dir, transcript):
    """AC negative: TAUSIK_SKIP_HOOKS=1 must leave no trace at all."""
    path = transcript([assistant_entry(cache_read=500_000)])
    monkeypatch.setenv("TAUSIK_SKIP_HOOKS", "1")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"transcript_path": path})))

    assert sensor.main() == 0
    assert read_state(project_dir, SESSION) == {}


def test_garbage_on_stdin_is_survivable(monkeypatch, project_dir):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))

    assert sensor.main() == 0


def test_non_tausik_directory_is_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"transcript_path": ""})))

    assert sensor.main() == 0
    assert not (tmp_path / ".tausik" / ".autoloop.json").exists()


def test_exit_request_survives_a_sensor_rewrite(monkeypatch, project_dir, add_task, transcript):
    """The sensor runs after exit_guard; clobbering its flag would re-arm a fired block."""
    add_task("t1", steps=[("a", True)])
    write_state(project_dir, SESSION, {"exit_requested": True, "exit_kind": "soft"})
    path = transcript([assistant_entry(cache_read=400_000)])

    run_hook(monkeypatch, project_dir, {"transcript_path": path})

    state = read_state(project_dir, SESSION)
    assert state["exit_requested"] is True
    assert state["exit_kind"] == "soft"
    assert state["percent"] == 40.0


# --- state file + config --------------------------------------------------


def test_write_state_is_atomic_and_leaves_no_temp(project_dir):
    assert write_state(project_dir, SESSION, {"percent": 12.5}) is True

    assert read_state(project_dir, SESSION)["percent"] == 12.5
    leftovers = list((project_dir / ".tausik").glob("*.tmp"))
    assert leftovers == []


def test_read_state_tolerates_a_corrupt_file(project_dir):
    (project_dir / ".tausik" / ".autoloop.json").write_text("{broken", encoding="utf-8")

    assert read_state(project_dir, SESSION) == {}


def test_config_defaults_when_absent(project_dir):
    config = load_config(project_dir)

    assert config["context_window"] == 1_000_000
    assert config["soft_threshold"] == 50.0
    assert config["hard_threshold"] == 75.0


@pytest.mark.parametrize("raw", ["{not json", '{"autoloop": "not a dict"}', "[]"])
def test_config_malformed_falls_back_to_defaults(project_dir, raw):
    (project_dir / ".tausik" / "config.json").write_text(raw, encoding="utf-8")

    assert load_config(project_dir)["soft_threshold"] == 50.0


def test_config_overrides_are_honoured(project_dir):
    (project_dir / ".tausik" / "config.json").write_text(
        json.dumps({"autoloop": {"context_window": 200_000, "soft_threshold": 45}}),
        encoding="utf-8",
    )

    config = load_config(project_dir)

    assert config["context_window"] == 200_000
    assert config["soft_threshold"] == 45.0
    assert config["hard_threshold"] == 75.0  # untouched key keeps its default


def test_config_rejects_nonsense_values(project_dir):
    """A zero or negative window would make every percentage meaningless."""
    (project_dir / ".tausik" / "config.json").write_text(
        json.dumps({"autoloop": {"context_window": 0, "soft_threshold": -5}}),
        encoding="utf-8",
    )

    config = load_config(project_dir)

    assert config["context_window"] == 1_000_000
    assert config["soft_threshold"] == 50.0


# --- outside a run there is nothing to measure -----------------------------


def test_without_a_declared_run_nothing_is_measured(project_dir, monkeypatch, tmp_path):
    """AC negative: a person opened a chat to do their own work. Measuring is
    watching, and nobody asked to be watched — no reading file appears at all."""
    path = str(tmp_path / "session.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(assistant_entry(cache_read=120_000)) + "\n")

    run_hook(monkeypatch, project_dir, {"transcript_path": path}, run=None)

    assert not (project_dir / ".tausik" / "autoloop").exists()


def test_a_stopped_run_stops_the_measuring(project_dir, monkeypatch, tmp_path):
    """The command that ends a run ends the watching with it — including the
    part that leaves files behind after everyone has gone home."""
    path = str(tmp_path / "session.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(assistant_entry(cache_read=120_000)) + "\n")
    run_hook(monkeypatch, project_dir, {"transcript_path": path})
    written = sorted(p.name for p in (project_dir / ".tausik" / "autoloop").iterdir())

    autoloop_chat_cycle.end_run(str(project_dir))
    run_hook(monkeypatch, project_dir, {"transcript_path": path}, run=None)

    assert written  # the first run did measure
    assert sorted(p.name for p in (project_dir / ".tausik" / "autoloop").iterdir()) == written


def test_the_anchor_gap_comes_from_the_config(project_dir):
    """AC-1: интервал якоря был зашит числом 1200, и вставший прогон стоял
    двадцать минут ночи, ради которой прогон и объявляли."""
    assert load_config(project_dir)["anchor_seconds"] == 600.0

    (project_dir / ".tausik" / "config.json").write_text(
        json.dumps({"autoloop": {"anchor_seconds": 300}}), encoding="utf-8"
    )

    assert load_config(project_dir)["anchor_seconds"] == 300.0


@pytest.mark.parametrize("nonsense", [0, -5, "десять минут", None])
def test_a_nonsense_anchor_gap_falls_back_to_the_default(project_dir, nonsense):
    """AC-3 НЕГАТИВНЫЙ: ноль или строка превратили бы якорь в подачу каждый
    тик — чат получал бы команду поверх команды."""
    (project_dir / ".tausik" / "config.json").write_text(
        json.dumps({"autoloop": {"anchor_seconds": nonsense}}), encoding="utf-8"
    )

    assert load_config(project_dir)["anchor_seconds"] == 600.0
