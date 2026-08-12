"""Two sessions, two states.

Regression cover for the defect the first real run exposed: an interactive
session and a supervised one were both writing one project-wide state file, so
the supervisor could read a measurement taken inside somebody else's
conversation and decide an iteration's fate from it.
"""

import argparse
import io
import json
import subprocess
import time
from pathlib import Path

import pytest
from conftest import assistant_entry

import autoloop_run as autoloop
from autoloop import exit_guard, sensor
from autoloop import autonomy as autonomy_mod
from autoloop.state import (
    UNKNOWN_SESSION,
    prune_state_files,
    read_state,
    safe_session_id,
    state_dir,
    state_path,
    write_state,
)

SESSION_A = "aaaaaaaa-1111-2222-3333-444444444444"
SESSION_B = "bbbbbbbb-5555-6666-7777-888888888888"


def run_sensor(monkeypatch, project_dir, transcript_path, session_id):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.delenv("TAUSIK_SKIP_HOOKS", raising=False)
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            json.dumps({"transcript_path": transcript_path, "session_id": session_id})
        ),
    )
    return sensor.main()


# --- file naming ----------------------------------------------------------


def test_each_session_gets_its_own_file(project_dir):
    assert state_path(project_dir, SESSION_A) != state_path(project_dir, SESSION_B)
    assert SESSION_A in state_path(project_dir, SESSION_A)


def test_missing_session_id_is_named_not_merged(project_dir):
    """An unknown session stays distinguishable instead of joining a shared file."""
    assert safe_session_id("") == UNKNOWN_SESSION
    assert safe_session_id(None) == UNKNOWN_SESSION
    assert UNKNOWN_SESSION in state_path(project_dir, None)


@pytest.mark.parametrize(
    "raw,expected_absent",
    [("../../etc/passwd", ".."), ("a/b", "/"), ("c\\d", "\\"), ("x:y", ":")],
)
def test_session_id_cannot_escape_the_state_directory(
    project_dir, raw, expected_absent
):
    """The id becomes a filename, so path characters must not survive it."""
    path = state_path(project_dir, raw)

    assert expected_absent not in Path(path).name
    assert Path(path).parent == Path(state_dir(project_dir))


# --- isolation ------------------------------------------------------------


def test_writes_do_not_overwrite_each_other(project_dir):
    write_state(project_dir, SESSION_A, {"percent": 10.0})
    write_state(project_dir, SESSION_B, {"percent": 90.0})

    assert read_state(project_dir, SESSION_A)["percent"] == 10.0
    assert read_state(project_dir, SESSION_B)["percent"] == 90.0


def test_absent_session_reads_empty_not_a_neighbour(project_dir):
    """The core of the defect: no reading must never silently become someone else's."""
    write_state(project_dir, SESSION_A, {"percent": 90.0})

    assert read_state(project_dir, SESSION_B) == {}


def test_two_sensor_runs_keep_separate_readings(
    monkeypatch, project_dir, add_task, transcript
):
    add_task("t1", steps=[("a", True)])
    small = transcript([assistant_entry(cache_read=100_000)])
    large = transcript([assistant_entry(cache_read=800_000)])

    run_sensor(monkeypatch, project_dir, small, SESSION_A)
    run_sensor(monkeypatch, project_dir, large, SESSION_B)

    assert read_state(project_dir, SESSION_A)["percent"] == 10.0
    assert read_state(project_dir, SESSION_B)["percent"] == 80.0


def test_exit_flag_of_one_session_does_not_silence_another(
    monkeypatch, capsys, project_dir, add_task, transcript
):
    """A block already fired in session A must not stop session B from firing."""
    add_task("t1", steps=[("a", True)])
    path = transcript([assistant_entry(cache_read=500_000)])
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.setenv("TAUSIK_AUTONOMY", "1")
    monkeypatch.delenv("TAUSIK_SKIP_HOOKS", raising=False)
    autonomy_mod.create_run_marker(str(project_dir), 1)
    write_state(project_dir, SESSION_A, {"exit_requested": True, "exit_kind": "soft"})

    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"transcript_path": path, "session_id": SESSION_B})),
    )
    exit_guard.main()

    decision = json.loads(capsys.readouterr().out)
    assert decision["decision"] == "block"
    assert read_state(project_dir, SESSION_B)["exit_requested"] is True


def test_interactive_session_does_not_move_the_supervisors_reading(
    monkeypatch, project_dir, add_task, transcript
):
    """The exact production shape: supervised child + a human's session, at once."""
    add_task("t1", steps=[("a", True)])
    child = transcript([assistant_entry(cache_read=310_000)])
    human = transcript([assistant_entry(cache_read=50_000)])

    run_sensor(monkeypatch, project_dir, child, SESSION_A)
    run_sensor(monkeypatch, project_dir, human, SESSION_B)  # human keeps working

    assert read_state(project_dir, SESSION_A)["percent"] == 31.0


# --- supervisor wiring ----------------------------------------------------


@pytest.fixture
def loop_env(monkeypatch, project_dir):
    monkeypatch.setattr(autoloop, "PROJECT_DIR", Path(project_dir))
    monkeypatch.setattr(autoloop, "tausik_cli", lambda _dir: "fake-tausik")
    calls = {"queue": ["task-a"], "session_id": SESSION_A, "claude": []}

    def fake_cli(project, args, timeout=30):
        if args[:2] == ["task", "list"]:
            status = args[args.index("--status") + 1]
            if status == "planning" and calls["queue"]:
                return "slug title status\n----\ntask-a Title planning\n"
            return "slug title status\n----\n"
        return ""

    def fake_run(cmd, **kwargs):
        if cmd and cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 1, "", "")
        calls["claude"].append(cmd)
        calls["queue"].clear()
        payload = {"is_error": False, "total_cost_usd": 0.1}
        if calls["session_id"] is not None:
            payload["session_id"] = calls["session_id"]
        return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

    monkeypatch.setattr(autoloop, "_run_cli", fake_cli)
    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def run_loop(**overrides):
    args = argparse.Namespace(
        command="run",
        max_iterations=None,
        max_idle=None,
        max_crashes=None,
        model=None,
        timeout=60,
        dry_run=False,
        git_mode="full",
        direction="",
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return autoloop.loop(args)


def test_supervisor_journals_the_reading_of_its_own_child(loop_env, project_dir):
    write_state(project_dir, SESSION_A, {"percent": 42.0, "exit_kind": "soft"})
    write_state(project_dir, SESSION_B, {"percent": 99.0, "exit_kind": "hard"})

    run_loop()

    import autoloop_journal as journal

    (entry,) = journal.read_entries(str(project_dir))
    assert entry["percent_at_exit"] == 42.0
    assert entry["exit_reason"] == "soft"


def test_child_without_a_session_id_yields_no_reading(loop_env, project_dir):
    """AC negative: unknown session records null, never a neighbour's number."""
    loop_env["session_id"] = None
    write_state(project_dir, SESSION_B, {"percent": 99.0, "exit_kind": "hard"})

    run_loop()

    import autoloop_journal as journal

    (entry,) = journal.read_entries(str(project_dir))
    assert entry["percent_at_exit"] is None
    assert entry["exit_reason"] == "session_unknown"


# --- housekeeping ---------------------------------------------------------


def test_old_state_files_are_pruned(project_dir):
    write_state(project_dir, SESSION_A, {"percent": 1.0})
    write_state(project_dir, SESSION_B, {"percent": 2.0})
    stale = Path(state_path(project_dir, SESSION_A))
    old = time.time() - 48 * 3600
    import os

    os.utime(stale, (old, old))

    assert prune_state_files(project_dir) == 1
    assert read_state(project_dir, SESSION_A) == {}
    assert read_state(project_dir, SESSION_B)["percent"] == 2.0


def test_pruning_leaves_fresh_files_alone(project_dir):
    write_state(project_dir, SESSION_A, {"percent": 1.0})

    assert prune_state_files(project_dir) == 0
    assert read_state(project_dir, SESSION_A)["percent"] == 1.0


def test_pruning_without_a_state_directory_is_harmless(tmp_path):
    assert prune_state_files(str(tmp_path)) == 0


def test_pruning_never_touches_permission_profiles(project_dir):
    """AC negative: the cleanup removes stale readings only.

    A profile is rewritten once per run, so after a day of a single long run it
    is older than the cutoff — sweeping it away would leave the next iteration
    running under the unrestricted base profile.
    """
    import os

    from autoloop.state import profiles_dir

    write_state(project_dir, SESSION_A, {"percent": 1.0})
    stale_reading = Path(state_path(project_dir, SESSION_A))
    profile = Path(profiles_dir(project_dir)) / "settings.git-off.json"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(json.dumps({"permissions": {"allow": []}}), encoding="utf-8")
    old = time.time() - 48 * 3600
    for path in (stale_reading, profile):
        os.utime(path, (old, old))

    assert prune_state_files(project_dir) == 1
    assert not stale_reading.exists()
    assert profile.exists()
