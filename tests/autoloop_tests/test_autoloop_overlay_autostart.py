"""Who opens the run window, and who must not.

In agents mode the window is the only place the run is visible: the chat is
free by design and the human is usually not in the room. Opening it was a
separate command (`/auto окно`) nobody remembered at the right moment, so runs
had a cat nobody ever saw. In the chat the opposite holds — the work is already
on screen, and a window there is a second copy of it.
"""

import argparse
import os
import subprocess
from pathlib import Path

import pytest

import autoloop_presence as presence
import autoloop_run as autoloop
from .conftest import FakeProcess


def run_loop(**overrides):
    args = argparse.Namespace(
        command="run",
        max_iterations=1,
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
    """A run that reaches exactly one iteration. Processes come from `spawned`."""
    monkeypatch.setattr(autoloop, "PROJECT_DIR", Path(project_dir))
    monkeypatch.setattr(autoloop, "tausik_cli", lambda _dir: "fake-tausik")
    claude = []

    def fake_cli(project, args, timeout=30):
        if args[:2] == ["task", "list"]:
            status = args[args.index("--status") + 1]
            if status == "planning":
                return "slug title status\n----\ntask-a Title planning\n"
        return "slug title status\n----\n"

    def fake_run(cmd, **kwargs):
        if cmd and cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 1, "", "")
        claude.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, '{"is_error": false}', "")

    monkeypatch.setattr(autoloop, "_run_cli", fake_cli)
    monkeypatch.setattr(subprocess, "run", fake_run)
    return claude


def windows(spawned):
    return [p for p in spawned if any("overlay" in str(part) for part in p.cmd)]


# --- the run puts its own window up ---------------------------------------


def test_an_agents_run_opens_the_window_itself(loop_env, spawned):
    run_loop()

    assert len(windows(spawned)) == 1


def test_the_run_neither_waits_on_the_window_nor_takes_it_down(loop_env, spawned):
    """A supervisor that waits on a window never runs an iteration, and one
    that closes it at the end deletes the report: a stopped run paints a
    sleeping cat, which is what the human comes back to."""
    run_loop()
    window = windows(spawned)[0]

    assert not window.waited
    assert not window.killed


def test_a_window_that_cannot_open_leaves_the_run_alone(loop_env, monkeypatch, capsys):
    """AC negative: headless boxes and ssh sessions have no display at all.
    Their runs are just as valid — the window is a nicety, not a precondition.
    """

    def refuse(cmd, *args, **kwargs):
        raise OSError("нет графической среды")

    monkeypatch.setattr(subprocess, "Popen", refuse)

    code = run_loop()

    assert code == autoloop.EXIT_STOPPED  # by the iteration cap, exactly as usual
    assert len(loop_env) == 1  # the iteration ran regardless
    assert "окно прогона не поднять" in capsys.readouterr().out


def test_no_second_window_goes_up_over_a_live_one(loop_env, spawned, project_dir):
    """AC negative: two windows paint the same numbers on top of each other.
    The check is by registered pid — this pytest process is `python` too, so a
    name check would call the test runner a window."""
    presence.claim_overlay(str(project_dir), os.getpid())

    run_loop()

    assert windows(spawned) == []


# --- the lock behind that check -------------------------------------------


def test_a_lock_left_by_a_dead_window_reads_as_no_window(project_dir):
    """Otherwise one crashed window would mean no run ever opens another."""
    presence.claim_overlay(str(project_dir), 4242)

    assert presence.overlay_is_open(str(project_dir), is_alive=lambda _pid: False) is False
    assert presence.overlay_is_open(str(project_dir), is_alive=lambda _pid: True) is True


def test_a_closed_window_leaves_nothing_claimed(project_dir):
    presence.claim_overlay(str(project_dir), os.getpid())
    presence.release_overlay(str(project_dir))

    assert presence.overlay_is_open(str(project_dir)) is False


def test_no_lock_at_all_means_no_window(project_dir):
    assert presence.overlay_is_open(str(project_dir)) is False


# --- and the chat mode stays as it was ------------------------------------


def test_the_chat_mode_opens_no_window(project_dir, spawned, monkeypatch):
    """AC negative: in the chat the run is the conversation the human is
    already reading. This guards the fix from leaking into the other mode."""
    import autoloop_command as command

    monkeypatch.setattr(command, "chat_pid", lambda: 999)  # reach the spawn below

    command.start(str(project_dir), "разгреби очередь")

    assert spawned, "наблюдатель не поднялся — тест перестал что-либо проверять"
    assert windows(spawned) == []


# --- the guard behind the whole file --------------------------------------


def test_the_suite_cannot_start_a_real_process(spawned):
    """This one is here because it already failed once. Only `subprocess.run`
    was stubbed, the window spawn goes through `Popen`, and three suite runs
    put 58 live tkinter windows on a real desktop — each surviving pytest
    because the window is built to outlive the run it watches.
    """
    process = subprocess.Popen([os.sys.executable, "-c", "pass"])

    assert isinstance(process, FakeProcess), "настоящий процесс запущен из теста"
    assert spawned[-1] is process
