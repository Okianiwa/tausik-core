"""Supervisor core: the loop that actually clears the context by ending a process."""

import argparse
import json
import subprocess
from pathlib import Path

import pytest

import autoloop_run as autoloop
from autoloop_brakes import BrakeState


@pytest.fixture
def loop_env(monkeypatch, project_dir):
    """Point the supervisor at a sandbox and stub out both external processes.

    `claude` and the TAUSIK CLI are the only things this module talks to, so
    faking exactly those two exercises everything else for real.
    """
    monkeypatch.setattr(autoloop, "PROJECT_DIR", Path(project_dir))
    monkeypatch.setattr(autoloop, "tausik_cli", lambda _dir: "fake-tausik")

    calls = {"claude": [], "queue": {"active": [], "planning": []}, "statuses": {}}

    def fake_cli(project, args, timeout=30):
        if args[:2] == ["task", "list"]:
            status = args[args.index("--status") + 1]
            slugs = calls["queue"].get(status, [])
            if not slugs:
                return "slug  title  status\n----\n"
            body = "\n".join(f"{slug}  Title  {status}" for slug in slugs)
            return f"slug  title  status\n----\n{body}\n"
        return ""

    monkeypatch.setattr(autoloop, "_run_cli", fake_cli)

    def fake_run(cmd, **kwargs):
        if cmd and cmd[0] == "git":
            # The journal asks git for new commits; that is not an iteration.
            return subprocess.CompletedProcess(cmd, 1, "", "not a repo")
        calls["claude"].append({"cmd": cmd, "env": kwargs.get("env", {})})
        behaviour = calls.get("behaviour")
        if behaviour:
            return behaviour(cmd, calls)
        # Default: the task gets closed, so the queue drains by one.
        if calls["queue"]["planning"]:
            calls["queue"]["planning"].pop(0)
        payload = json.dumps(
            {"is_error": False, "total_cost_usd": 0.01, "session_id": "s"}
        )
        return subprocess.CompletedProcess(cmd, 0, payload, "")

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


# --- queue handling -------------------------------------------------------


def test_empty_queue_exits_without_launching_claude(loop_env, capsys):
    """AC negative: nothing to do means no process, no marker, exit 0."""
    code = run_loop()

    assert code == 0
    assert loop_env["claude"] == []
    assert "очередь пуста" in capsys.readouterr().out


def test_runs_one_iteration_per_task(loop_env):
    loop_env["queue"]["planning"] = ["task-a", "task-b"]

    code = run_loop()

    assert code == 0
    assert len(loop_env["claude"]) == 2


def test_active_task_wins_over_planning(loop_env):
    """An active task is unfinished business — finish it before starting new work."""
    loop_env["queue"]["active"] = ["half-done"]
    loop_env["queue"]["planning"] = ["fresh"]
    loop_env["behaviour"] = lambda cmd, calls: (
        calls["queue"]["active"].clear()
        or subprocess.CompletedProcess(cmd, 0, '{"is_error": false}', "")
    )

    run_loop()

    assert "half-done" in loop_env["claude"][0]["cmd"][2]


def test_task_slugs_are_parsed_from_the_cli_table(loop_env):
    """AC: the queue comes from the TAUSIK CLI, not from raw sqlite."""
    loop_env["queue"]["planning"] = ["alpha", "beta"]

    assert autoloop.list_task_slugs(Path("."), "planning") == ["alpha", "beta"]
    assert autoloop.list_task_slugs(Path("."), "blocked") == []


# --- the child process ----------------------------------------------------


def test_child_gets_autonomy_flag_and_no_skip_hooks(loop_env, monkeypatch):
    """Autonomy without gates is refused — the driver must not pass SKIP_HOOKS on."""
    monkeypatch.setenv("TAUSIK_SKIP_HOOKS", "1")
    loop_env["queue"]["planning"] = ["task-a"]

    run_loop()

    env = loop_env["claude"][0]["env"]
    assert env["TAUSIK_AUTONOMY"] == "1"
    assert "TAUSIK_SKIP_HOOKS" not in env


def test_child_uses_the_autonomy_settings_profile(project_dir, monkeypatch):
    settings = project_dir / ".claude"
    settings.mkdir()
    (settings / "settings.autonomy.json").write_text("{}", encoding="utf-8")

    cmd = autoloop.claude_command("prompt", Path(project_dir), model="claude-opus-5")

    assert "--settings" in cmd
    # Derived from settings.autonomy.json rather than the file itself: the
    # generated copy is what carries the session guard.
    assert cmd[cmd.index("--settings") + 1].endswith("settings.git-full.json")
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5"
    assert "--dangerously-skip-permissions" not in cmd


def test_prompt_carries_task_and_threshold():
    prompt = autoloop.build_prompt("my-task", {"soft_threshold": 30})

    assert "my-task" in prompt
    assert "30%" in prompt
    assert "verify" in prompt
    assert "не начинай следующую задачу" in prompt.lower()


def test_prompt_falls_back_when_template_is_missing(tmp_path):
    prompt = autoloop.build_prompt("my-task", {}, template_path=tmp_path / "gone.md")

    assert "my-task" in prompt


@pytest.mark.parametrize(
    "stdout,expected_cost",
    [
        ('{"is_error": false, "total_cost_usd": 0.5}', 0.5),
        ('noise\n{"is_error": false, "total_cost_usd": 0.25}', 0.25),
        ("not json at all", 0.0),
        ("", 0.0),
    ],
)
def test_result_parsing_is_forgiving(loop_env, project_dir, stdout, expected_cost):
    loop_env["queue"]["planning"] = ["task-a"]
    loop_env["behaviour"] = lambda cmd, calls: (
        calls["queue"]["planning"].clear()
        or subprocess.CompletedProcess(cmd, 0, stdout, "")
    )

    run_loop()  # must not raise on any of these shapes

    # A throwaway project, not ".": run_iteration builds the settings profile
    # as a side effect, and pointed at the repo it writes one into the working
    # tree of whoever runs the suite.
    result = autoloop.run_iteration(Path(project_dir), "t", {}, None, 5)
    assert result["cost_usd"] == expected_cost


def test_timeout_is_reported_not_raised(monkeypatch, project_dir):
    def hang(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 10)

    monkeypatch.setattr(subprocess, "run", hang)

    result = autoloop.run_iteration(Path(project_dir), "t", {}, None, 10)

    assert result["returncode"] == -1
    assert result["error"] == "timeout"


def test_missing_claude_binary_is_reported(monkeypatch, project_dir):
    def missing(cmd, **kwargs):
        raise OSError("no such file")

    monkeypatch.setattr(subprocess, "run", missing)

    result = autoloop.run_iteration(Path(project_dir), "t", {}, None, 10)

    assert result["returncode"] == -1
    assert "spawn_failed" in result["error"]


# --- run marker -----------------------------------------------------------


def test_marker_exists_during_the_run_and_is_removed_after(loop_env, project_dir):
    """AC: a stale marker would arm autonomy in the next interactive session."""
    marker = Path(project_dir) / ".tausik" / ".autoloop.run"
    loop_env["queue"]["planning"] = ["task-a"]
    seen = {}

    def observe(cmd, calls):
        seen["during"] = marker.exists()
        calls["queue"]["planning"].clear()
        return subprocess.CompletedProcess(cmd, 0, '{"is_error": false}', "")

    loop_env["behaviour"] = observe

    run_loop()

    assert seen["during"] is True
    assert marker.exists() is False


def test_marker_is_removed_even_when_an_iteration_explodes(loop_env, project_dir):
    marker = Path(project_dir) / ".tausik" / ".autoloop.run"
    loop_env["queue"]["planning"] = ["task-a"]

    def explode(cmd, calls):
        raise RuntimeError("boom")

    loop_env["behaviour"] = explode

    with pytest.raises(RuntimeError):
        run_loop()

    assert marker.exists() is False


def test_dry_run_shows_the_command_without_spawning(loop_env, capsys):
    loop_env["queue"]["planning"] = ["task-a"]

    code = run_loop(dry_run=True)

    assert code == 0
    assert loop_env["claude"] == []
    assert "dry-run" in capsys.readouterr().out


def test_missing_tausik_cli_stops_before_anything_else(monkeypatch, project_dir):
    monkeypatch.setattr(autoloop, "PROJECT_DIR", Path(project_dir))
    monkeypatch.setattr(autoloop, "tausik_cli", lambda _dir: None)

    assert run_loop() == autoloop.EXIT_STOPPED


# --- brake wiring ---------------------------------------------------------


def test_iteration_cap_is_honoured(loop_env):
    """The queue never drains here — only the cap can end this loop."""
    loop_env["queue"]["planning"] = ["endless"]
    loop_env["behaviour"] = lambda cmd, calls: subprocess.CompletedProcess(
        cmd, 0, '{"is_error": false}', ""
    )

    code = run_loop(max_iterations=3)

    assert len(loop_env["claude"]) == 3
    assert code == autoloop.EXIT_STOPPED


def test_brake_state_counts_streaks():
    state = BrakeState()

    state.record(task_slug="t", crashed=True, progressed=False)
    state.record(task_slug="t", crashed=True, progressed=False)

    assert state.crash_streak == 2
    assert state.idle_streak == 2

    state.record(task_slug="t", crashed=False, progressed=True)

    assert state.crash_streak == 0
    assert state.idle_streak == 0
