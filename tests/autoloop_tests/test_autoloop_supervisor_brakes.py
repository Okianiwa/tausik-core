"""Brakes: every reason the loop is allowed to stop itself.

A loop nobody watches has no natural end — these are the ends we gave it.
"""

import argparse
import subprocess
from pathlib import Path

import pytest

import autoloop_run as autoloop
from autoloop import autonomy
from autoloop_brakes import BrakeState, check_brakes, stop_switch_path


def state(**kwargs) -> BrakeState:
    return BrakeState(**kwargs)


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


@pytest.fixture
def loop_env(monkeypatch, project_dir):
    """Sandbox with a queue that never drains — only a brake can end the loop."""
    monkeypatch.setattr(autoloop, "PROJECT_DIR", Path(project_dir))
    monkeypatch.setattr(autoloop, "tausik_cli", lambda _dir: "fake-tausik")

    calls = {"claude": [], "exit_codes": [], "statuses": ["planning"]}

    def fake_cli(project, args, timeout=30):
        if args[:2] == ["task", "list"]:
            status = args[args.index("--status") + 1]
            # The task sits in whatever status the test last set, so "no
            # progress" is the default unless a test moves it.
            if status == calls["statuses"][-1]:
                return "slug title status\n----\nstuck-task Title x\n"
            return "slug title status\n----\n"
        return ""

    def fake_run(cmd, **kwargs):
        if cmd and cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 1, "", "not a repo")
        calls["claude"].append(cmd)
        code = calls["exit_codes"].pop(0) if calls["exit_codes"] else 0
        stdout = '{"is_error": false}' if code == 0 else ""
        return subprocess.CompletedProcess(cmd, code, stdout, "boom" if code else "")

    monkeypatch.setattr(autoloop, "_run_cli", fake_cli)
    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


# --- kill switch ----------------------------------------------------------


def test_stop_file_halts_before_the_next_iteration(project_dir):
    stop_switch_path(project_dir).write_text("", encoding="utf-8")

    verdict = check_brakes(project_dir, state())

    assert verdict.stop is True
    assert verdict.clean is True  # a requested stop is not a failure
    assert "autoloop.stop" in verdict.reason


def test_absent_stop_file_lets_the_loop_continue(project_dir):
    """The other branch: without the file nothing halts."""
    assert check_brakes(project_dir, state()).stop is False


def test_kill_switch_stops_the_running_loop(loop_env, project_dir, monkeypatch, capsys):
    """The switch is honoured between iterations, not mid-flight."""
    seen = {"n": 0}

    def fake_run(cmd, **kwargs):
        if cmd and cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 1, "", "not a repo")
        loop_env["claude"].append(cmd)
        seen["n"] += 1
        if seen["n"] == 2:  # human flips the switch during the second run
            stop_switch_path(project_dir).write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, '{"is_error": false}', "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    code = run_loop(max_iterations=10)

    assert code == 0
    assert len(loop_env["claude"]) == 2  # finished the current one, started no third
    assert "остановка по требованию" in capsys.readouterr().out


def test_stale_stop_file_does_not_kill_the_new_run(loop_env, project_dir, capsys):
    """A switch flipped at a run that has already ended is not an order for
    this one. Before the fix the supervisor started, read it, stopped, and
    exited 0 — indistinguishable from a run that simply had nothing to do.
    """
    stop_switch_path(project_dir).write_text("", encoding="utf-8")

    code = run_loop(max_iterations=1)

    assert len(loop_env["claude"]) == 1
    assert code == autoloop.EXIT_STOPPED  # stopped by the cap, not by the switch
    assert not stop_switch_path(project_dir).exists()
    assert "снята метка остановки" in capsys.readouterr().out


def test_unremovable_stop_file_refuses_the_run_rather_than_walking_into_it(
    loop_env, project_dir, monkeypatch, capsys
):
    """AC negative: if the file will not go, starting anyway reproduces the
    exact silent death the fix exists to remove. Refuse, say why, and leave no
    run marker behind for the next supervisor to trip over.
    """
    stop_switch_path(project_dir).write_text("", encoding="utf-8")

    def refuse(self, *args, **kwargs):
        raise OSError("в доступе отказано")

    monkeypatch.setattr(Path, "unlink", refuse)

    code = run_loop(max_iterations=1)

    assert code == autoloop.EXIT_STOPPED
    assert loop_env["claude"] == []
    assert "не снять" in capsys.readouterr().out
    assert not Path(autonomy.run_marker_path(str(project_dir))).exists()


def test_refusing_to_start_leaves_the_live_runs_switch_alone(loop_env, project_dir):
    """AC negative: another run is going and its owner has already asked it to
    stop. The supervisor that refuses to join must not take that request away
    on its way out — clearing happens past both refusals, never before them.
    """
    autonomy.create_run_marker(str(project_dir), 999)
    switch = stop_switch_path(project_dir)
    switch.write_text("", encoding="utf-8")

    assert run_loop(max_iterations=1) == autoloop.EXIT_STOPPED
    assert switch.exists()


# --- iteration cap --------------------------------------------------------


def test_iteration_cap_trips_with_a_nonzero_exit(tmp_path):
    verdict = check_brakes(tmp_path, state(iteration=20, max_iterations=20))

    assert verdict.stop is True
    assert verdict.clean is False
    assert "20" in verdict.reason


def test_below_the_cap_the_loop_continues(tmp_path):
    assert check_brakes(tmp_path, state(iteration=19, max_iterations=20)).stop is False


def test_cap_is_enforced_end_to_end(loop_env):
    code = run_loop(max_iterations=3)

    assert len(loop_env["claude"]) == 3
    assert code == autoloop.EXIT_STOPPED


# --- crash streak ---------------------------------------------------------


def test_two_crashes_in_a_row_stop_the_loop(tmp_path):
    verdict = check_brakes(tmp_path, state(crash_streak=2, last_task="t"))

    assert verdict.stop is True
    assert verdict.clean is False
    assert "t" in verdict.reason


def test_a_single_crash_does_not_stop_the_loop(tmp_path):
    """AC: one failure is noise; the next iteration still gets to run."""
    assert check_brakes(tmp_path, state(crash_streak=1)).stop is False


def test_single_crash_is_followed_by_another_iteration(loop_env):
    """End-to-end version of the same rule, through the real driver."""
    loop_env["exit_codes"] = [1, 0, 0]

    run_loop(max_iterations=3)

    assert len(loop_env["claude"]) == 3


def test_two_crashes_end_to_end(loop_env, capsys):
    loop_env["exit_codes"] = [1, 1, 0, 0]

    code = run_loop(max_iterations=10)

    assert len(loop_env["claude"]) == 2
    assert code == autoloop.EXIT_STOPPED
    assert "неудачных запусков" in capsys.readouterr().out


def test_a_success_resets_the_crash_streak(tmp_path):
    tally = state()
    tally.record(task_slug="t", crashed=True, progressed=False)
    tally.record(task_slug="t", crashed=False, progressed=True)
    tally.record(task_slug="t", crashed=True, progressed=False)

    assert tally.crash_streak == 1
    assert check_brakes(tmp_path, tally).stop is False


# --- no-progress detector -------------------------------------------------


def test_three_idle_iterations_stop_the_loop(tmp_path):
    verdict = check_brakes(tmp_path, state(idle_streak=3, last_task="stuck-task"))

    assert verdict.stop is True
    assert verdict.clean is False
    assert "stuck-task" in verdict.reason


def test_two_idle_iterations_are_tolerated(tmp_path):
    assert check_brakes(tmp_path, state(idle_streak=2)).stop is False


def test_progress_resets_the_idle_counter():
    tally = state()
    tally.record(task_slug="t", crashed=False, progressed=False)
    tally.record(task_slug="t", crashed=False, progressed=False)
    tally.record(task_slug="t", crashed=False, progressed=True)

    assert tally.idle_streak == 0


def test_stuck_task_stops_the_loop_end_to_end(loop_env, capsys):
    """The queue never changes status, so every iteration is idle."""
    code = run_loop(max_iterations=10)

    assert code == autoloop.EXIT_STOPPED
    assert len(loop_env["claude"]) == 3
    assert "топчется" in capsys.readouterr().out


# --- thresholds -----------------------------------------------------------


def test_config_supplies_the_thresholds():
    brakes = BrakeState.from_config(
        {"max_iterations": 7, "max_idle_iterations": 5, "max_crash_streak": 4}
    )

    assert (
        brakes.max_iterations,
        brakes.max_idle_iterations,
        brakes.max_crash_streak,
    ) == (7, 5, 4)


def test_cli_flags_win_over_config():
    brakes = BrakeState.from_config({"max_iterations": 7}, max_iterations=2)

    assert brakes.max_iterations == 2


def test_absent_flags_do_not_shadow_config():
    """argparse hands us None for a flag nobody passed — it must not win."""
    brakes = BrakeState.from_config({"max_iterations": 7}, max_iterations=None)

    assert brakes.max_iterations == 7


@pytest.mark.parametrize("bad", [0, -1])
def test_nonpositive_limits_are_clamped(bad):
    """A zero limit would disable the brake — the one mistake that runs away."""
    brakes = BrakeState.from_config(
        {"max_iterations": bad, "max_idle_iterations": bad, "max_crash_streak": bad}
    )

    assert brakes.max_iterations >= 1
    assert brakes.max_idle_iterations >= 1
    assert brakes.max_crash_streak >= 1


def test_defaults_when_config_is_empty():
    brakes = BrakeState.from_config({})

    assert (
        brakes.max_iterations,
        brakes.max_idle_iterations,
        brakes.max_crash_streak,
    ) == (20, 3, 2)


def test_config_thresholds_reach_the_loop(loop_env, project_dir):
    import json

    (project_dir / ".tausik" / "config.json").write_text(
        json.dumps({"autoloop": {"max_iterations": 2}}), encoding="utf-8"
    )

    run_loop()

    assert len(loop_env["claude"]) == 2
