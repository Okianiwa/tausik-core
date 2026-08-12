"""Exit protocol: when the loop ends a session, and when it must not."""

import io
import json

import pytest
from conftest import assistant_entry

from autoloop import autonomy as autonomy_mod
from autoloop import exit_guard
from autoloop.exit_guard import EXIT_HARD, EXIT_SOFT, decide
from autoloop.state import (
    STATE_COMPLETE,
    STATE_IDLE,
    STATE_IN_PROGRESS,
    load_config,
    read_state,
    write_state,
)

CONFIG = {"soft_threshold": 30.0, "hard_threshold": 75.0}
SESSION = "test-session"


def capture(
    monkeypatch, project_dir, payload, autonomy=True, capsys=None, want_err=False
):
    """Run the hook and return (exit_code, decision_dict_or_None).

    readouterr() drains both streams, so stderr has to be handed back from the
    same call rather than read again by the test.
    """
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.delenv("TAUSIK_SKIP_HOOKS", raising=False)
    if autonomy:
        # Flag AND run marker: autonomy.status() refuses one without the other.
        monkeypatch.setenv("TAUSIK_AUTONOMY", "1")
        autonomy_mod.create_run_marker(str(project_dir), 1234)
    else:
        monkeypatch.delenv("TAUSIK_AUTONOMY", raising=False)
        autonomy_mod.remove_run_marker(str(project_dir))
    payload.setdefault("session_id", SESSION)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    code = exit_guard.main()
    captured = capsys.readouterr() if capsys else None
    out = captured.out if captured else ""
    decision = json.loads(out) if out.strip() else None
    if want_err:
        return code, decision, (captured.err if captured else "")
    return code, decision


# --- threshold logic ------------------------------------------------------


@pytest.mark.parametrize(
    "percent,task_state,expected",
    [
        (29.9, STATE_COMPLETE, None),  # under soft: keep going
        (30.0, STATE_COMPLETE, EXIT_SOFT),  # exactly at soft counts
        (50.0, STATE_COMPLETE, EXIT_SOFT),
        (50.0, STATE_IN_PROGRESS, None),  # soft waits for a seam
        (50.0, STATE_IDLE, None),
        (75.0, STATE_IN_PROGRESS, EXIT_HARD),  # hard ignores task state
        (90.0, STATE_IDLE, EXIT_HARD),
        (90.0, STATE_COMPLETE, EXIT_HARD),  # hard wins over soft
    ],
)
def test_threshold_matrix(percent, task_state, expected):
    assert decide({"percent": percent, "task_state": task_state}, CONFIG) == expected


def test_unmeasurable_context_never_triggers_an_exit():
    """A broken transcript must not be read as 'the window is full'."""
    assert decide({"percent": None, "task_state": STATE_COMPLETE}, CONFIG) is None
    assert decide({"task_state": STATE_COMPLETE}, CONFIG) is None


def test_thresholds_come_from_config(project_dir):
    (project_dir / ".tausik" / "config.json").write_text(
        json.dumps({"autoloop": {"soft_threshold": 10, "hard_threshold": 20}}),
        encoding="utf-8",
    )
    config = load_config(project_dir)

    assert decide({"percent": 12.0, "task_state": STATE_COMPLETE}, config) == EXIT_SOFT
    assert (
        decide({"percent": 25.0, "task_state": STATE_IN_PROGRESS}, config) == EXIT_HARD
    )


# --- hook behaviour -------------------------------------------------------


def test_blocks_at_soft_threshold_on_a_finished_task(
    monkeypatch, capsys, project_dir, add_task, transcript
):
    add_task("t1", steps=[("a", True)])
    path = transcript([assistant_entry(cache_read=310_000)])

    code, decision = capture(
        monkeypatch, project_dir, {"transcript_path": path}, capsys=capsys
    )

    assert code == 0
    assert decision["decision"] == "block"
    assert "31.0%" in decision["reason"]
    assert "t1" in decision["reason"]


def test_hard_threshold_demands_a_handoff_mid_task(
    monkeypatch, capsys, project_dir, add_task, transcript
):
    add_task("t1", steps=[("a", False)])
    path = transcript([assistant_entry(cache_read=800_000)])

    _, decision = capture(
        monkeypatch, project_dir, {"transcript_path": path}, capsys=capsys
    )

    assert decision["decision"] == "block"
    assert "handoff" in decision["reason"].lower()
    assert read_state(project_dir, SESSION)["exit_kind"] == EXIT_HARD


def test_no_block_below_threshold(
    monkeypatch, capsys, project_dir, add_task, transcript
):
    add_task("t1", steps=[("a", True)])
    path = transcript([assistant_entry(cache_read=100_000)])

    code, decision = capture(
        monkeypatch, project_dir, {"transcript_path": path}, capsys=capsys
    )

    assert code == 0
    assert decision is None
    assert read_state(project_dir, SESSION).get("exit_requested") is None


def test_second_stop_does_not_block_again(
    monkeypatch, capsys, project_dir, add_task, transcript
):
    """AC negative: block→block would trap the process in a loop it cannot leave."""
    add_task("t1", steps=[("a", True)])
    path = transcript([assistant_entry(cache_read=400_000)])

    _, first = capture(
        monkeypatch, project_dir, {"transcript_path": path}, capsys=capsys
    )
    _, second = capture(
        monkeypatch, project_dir, {"transcript_path": path}, capsys=capsys
    )

    assert first["decision"] == "block"
    assert second is None


def test_stop_hook_active_flag_is_honoured(
    monkeypatch, capsys, project_dir, add_task, transcript
):
    """The harness's own re-entry marker is the first line of loop defence."""
    add_task("t1", steps=[("a", True)])
    path = transcript([assistant_entry(cache_read=900_000)])

    _, decision = capture(
        monkeypatch,
        project_dir,
        {"transcript_path": path, "stop_hook_active": True},
        capsys=capsys,
    )

    assert decision is None


def test_a_closed_session_no_longer_silences_the_guard(
    monkeypatch, capsys, project_dir, add_task, transcript, close_session
):
    """The guard used to skip a closed session, reading it as "the agent has
    already left". Iterations no longer close the session at all, so a closed
    one now means the human ended theirs — and the run must still be recycled
    when its context fills up.
    """
    add_task("t1", steps=[("a", True)])
    close_session()
    path = transcript([assistant_entry(cache_read=500_000)])

    _, decision = capture(
        monkeypatch, project_dir, {"transcript_path": path}, capsys=capsys
    )

    assert decision["decision"] == "block"
    assert "Сессию НЕ закрывай" in decision["reason"]


def test_interactive_session_gets_a_note_not_a_block(
    monkeypatch, capsys, project_dir, add_task, transcript
):
    """AC: without TAUSIK_AUTONOMY the guard must never hijack a human's turn."""
    add_task("t1", steps=[("a", True)])
    path = transcript([assistant_entry(cache_read=500_000)])

    code, decision, err = capture(
        monkeypatch,
        project_dir,
        {"transcript_path": path},
        autonomy=False,
        capsys=capsys,
        want_err=True,
    )

    assert code == 0
    assert decision is None
    assert "autoloop" in err
    assert read_state(project_dir, SESSION).get("exit_requested") is None


def test_skip_hooks_disables_the_guard(
    monkeypatch, capsys, project_dir, add_task, transcript
):
    add_task("t1", steps=[("a", True)])
    path = transcript([assistant_entry(cache_read=900_000)])
    monkeypatch.setenv("TAUSIK_SKIP_HOOKS", "1")
    monkeypatch.setenv("TAUSIK_AUTONOMY", "1")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"transcript_path": path, "session_id": SESSION})),
    )

    assert exit_guard.main() == 0
    assert capsys.readouterr().out == ""


def test_garbage_stdin_is_survivable(monkeypatch, project_dir):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.setenv("TAUSIK_AUTONOMY", "1")
    monkeypatch.setattr("sys.stdin", io.StringIO("]["))

    assert exit_guard.main() == 0


# --- supervisor-facing signal --------------------------------------------


def test_state_tells_the_supervisor_why_the_process_ended(
    monkeypatch, capsys, project_dir, add_task, transcript
):
    """AC: 'ran out of context' must be distinguishable from 'no work left'."""
    add_task("t1", steps=[("a", True)])
    path = transcript([assistant_entry(cache_read=350_000)])

    capture(monkeypatch, project_dir, {"transcript_path": path}, capsys=capsys)
    state = read_state(project_dir, SESSION)

    assert state["exit_requested"] is True
    assert state["exit_kind"] == EXIT_SOFT
    assert state["task_state"] == STATE_COMPLETE
    assert state["percent"] == 35.0


def test_idle_state_is_recorded_when_no_task_is_active(
    monkeypatch, project_dir, add_task, transcript
):
    """No active task + no block = the supervisor's 'queue is empty' signal."""
    add_task("t1", status="done", steps=[("a", True)])
    path = transcript([assistant_entry(cache_read=120_000)])
    write_state(project_dir, SESSION, {})

    from autoloop import sensor

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.delenv("TAUSIK_SKIP_HOOKS", raising=False)
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"transcript_path": path, "session_id": SESSION})),
    )
    sensor.main()

    assert read_state(project_dir, SESSION)["task_state"] == STATE_IDLE
