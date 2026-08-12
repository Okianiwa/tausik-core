"""The session belongs to the human; an iteration is smaller than a session.

Regression cover for the third instance of one defect class: a per-project
entity used where a per-run one was needed. `autoloop-state-isolation` had the
state file, `autoloop-profile-collision` had the generated profile, and here it
is the TAUSIK session — an iteration wrote its handoff onto the human's session
row and then ended the session, so a human who walked away mid-session came
back to a closed one with someone else's notes on it.
"""

import argparse
import io
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

import autoloop_git
import autoloop_handoff as handoff
import autoloop_journal as journal
import autoloop_run as autoloop
from autoloop import autonomy, session_guard
from conftest import SCRIPTS_DIR

from autoloop.state import read_session_row
from autoloop_child import build_prompt

SESSION_A = "aaaaaaaa-1111-2222-3333-444444444444"

PROMPT_PATH = SCRIPTS_DIR / "autoloop_prompt.md"


def prompt_text():
    return build_prompt("my-task", {"soft_threshold": 30}, template_path=PROMPT_PATH)


def session_rows(project_dir):
    conn = sqlite3.connect(Path(project_dir) / ".tausik" / "tausik.db")
    try:
        return conn.execute(
            "SELECT id, ended_at, handoff FROM sessions ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


# --- AC 2: what the prompt tells the iteration ----------------------------


SESSION_END_MENTIONS = ("/end", "session end", "session_end", "сессию")

# The prompt has to name the thing it forbids, so a mention is not by itself
# the defect — an *instruction* is. Every line that names session-closing must
# carry a negation; asserting the words are absent would only teach the next
# author to spell them differently.
NEGATIONS = ("не вызывай", "не закрывай", "не трогай", "отклонена", "не в сессию")


def test_prompt_never_asks_the_iteration_to_end_the_session():
    """AC 2: the instruction that caused the defect is gone from the prompt."""
    for line in prompt_text().lower().splitlines():
        if not any(mention in line for mention in SESSION_END_MENTIONS):
            continue
        assert any(word in line for word in NEGATIONS), line

    assert "заверши сессию" not in prompt_text().lower()
    assert "закрой сессию" not in prompt_text().lower()


def test_prompt_says_the_session_belongs_to_the_human():
    text = prompt_text().lower()

    assert "принадлежит человеку" in text
    assert "autoloop_handoff.py write" in text


def test_prompt_does_not_send_the_iteration_through_start():
    """AC 5: /start opens a session when none exists — that is the run opening
    a session for itself by the back door."""
    text = prompt_text().lower()

    assert "выполни `/start`" not in text
    assert "не вызывай `/start`" in text


def test_prompt_still_carries_the_verify_first_contract():
    """The rewrite must not drop the parts that were working."""
    text = prompt_text()

    assert "--relevant-files" in text
    assert "verify --task" in text
    assert "--ac-verified" in text


def test_fallback_prompt_also_leaves_the_session_alone(tmp_path):
    """A missing template must not resurrect the old instruction."""
    text = build_prompt("my-task", {}, template_path=tmp_path / "gone.md").lower()

    assert "заверши сессию" not in text
    assert "autoloop_handoff.py write" in text


# --- AC 1 + 4: the guard --------------------------------------------------


@pytest.mark.parametrize(
    "tool_name,tool_input",
    [
        ("mcp__tausik-project__tausik_session_end", {}),
        ("Bash", {"command": ".tausik/tausik session end"}),
        ("Bash", {"command": ".tausik/tausik.cmd session end --summary 'done'"}),
        ("PowerShell", {"command": "& .tausik/tausik.cmd session_end"}),
    ],
)
def test_guard_recognises_every_way_to_close_the_session(tool_name, tool_input):
    assert session_guard.is_session_end(tool_name, tool_input)


@pytest.mark.parametrize(
    "tool_name,tool_input",
    [
        ("Bash", {"command": ".tausik/tausik task done my-task --ac-verified"}),
        ("Bash", {"command": "grep -rn 'session end' docs/"}),
        ("Bash", {"command": "python -m pytest tests/"}),
        ("mcp__tausik-project__tausik_task_done", {}),
        ("Read", {"file_path": "session_end.py"}),
    ],
)
def test_guard_leaves_unrelated_calls_alone(tool_name, tool_input):
    """AC negative: a guard that blocks ordinary work would stop the run dead."""
    assert not session_guard.is_session_end(tool_name, tool_input)


@pytest.fixture
def autonomous(monkeypatch, project_dir):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.setenv("TAUSIK_AUTONOMY", "1")
    monkeypatch.delenv("TAUSIK_SKIP_HOOKS", raising=False)
    autonomy.create_run_marker(str(project_dir), 1)
    return project_dir


def run_guard(monkeypatch, tool_name, tool_input, session_id=SESSION_A):
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            json.dumps(
                {
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "session_id": session_id,
                }
            )
        ),
    )
    return session_guard.main()


def test_guard_blocks_and_journals_the_attempt(monkeypatch, capsys, autonomous):
    """AC 4: the attempt does not pass in silence — it lands in the journal."""
    add = sqlite3.connect(Path(autonomous) / ".tausik" / "tausik.db")
    add.execute("INSERT INTO tasks (slug, status) VALUES ('my-task', 'active')")
    add.commit()
    add.close()

    code = run_guard(monkeypatch, "mcp__tausik-project__tausik_session_end", {})

    assert code == 2
    assert "сессия принадлежит человеку" in capsys.readouterr().err
    (event,) = journal.read_events(str(autonomous), journal.EVENT_SESSION_END_BLOCKED)
    assert event["tool"] == "mcp__tausik-project__tausik_session_end"
    assert event["task_slug"] == "my-task"
    assert event["session_id"] == SESSION_A


def test_blocked_attempt_shows_up_in_the_report(autonomous, monkeypatch):
    """AC 4: journalled is not enough — the human reads the report, not the log."""
    run_guard(monkeypatch, "Bash", {"command": ".tausik/tausik session end"})
    journal.open_iteration(str(autonomous), 1, "my-task", {})

    report = journal.format_report(str(autonomous))

    assert "граница сессии" in report
    assert "пыталась закрыть сессию" in report


@pytest.mark.parametrize("exit_reason", ["soft", "hard", "completed"])
def test_a_recycled_iteration_is_not_reported_as_a_failure(project_dir, exit_reason):
    """Hitting the context threshold is the designed exit. Calling it a сбой
    buries the lines that matter — including the session ones above."""
    entry = journal.open_iteration(str(project_dir), 1, "my-task", {})
    journal.close_iteration(
        str(project_dir), entry, exit_reason=exit_reason, status_after="done"
    )

    assert journal.summarize(str(project_dir))["failed"] == []
    assert "сбой" not in journal.format_report(str(project_dir))


def test_a_real_failure_is_still_reported(project_dir):
    entry = journal.open_iteration(str(project_dir), 1, "my-task", {})
    journal.close_iteration(str(project_dir), entry, exit_reason="timeout")

    assert "сбой" in journal.format_report(str(project_dir))


def test_guard_stays_out_of_an_interactive_session(monkeypatch, project_dir):
    """AC negative: outside a run, ending the session is the human's own call."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.delenv("TAUSIK_AUTONOMY", raising=False)

    code = run_guard(monkeypatch, "mcp__tausik-project__tausik_session_end", {})

    assert code == 0
    assert journal.read_events(str(project_dir)) == []


def test_guard_survives_a_project_without_a_journal_directory(monkeypatch, tmp_path):
    """A hook must never be the thing that breaks the tool call it guards."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("TAUSIK_AUTONOMY", raising=False)

    assert run_guard(monkeypatch, "Bash", {"command": "tausik session end"}) == 0


def test_generated_profile_carries_the_guard(project_dir):
    """AC 1: the ban reaches the child without touching .claude/settings.json."""
    claude_dir = project_dir / ".claude"
    claude_dir.mkdir(exist_ok=True)
    (claude_dir / "settings.autonomy.json").write_text(
        json.dumps({"permissions": {"allow": ["Write"]}}), encoding="utf-8"
    )

    for mode in (autoloop_git.GIT_FULL, autoloop_git.GIT_OFF):
        profile = json.loads(
            Path(autoloop_git.build_profile(str(project_dir), mode)).read_text(
                encoding="utf-8"
            )
        )
        wired = json.dumps(profile["hooks"]["PreToolUse"], ensure_ascii=False)

        assert "session_guard.py" in wired, mode
        assert "tausik_session_end" in wired, mode


def test_guard_is_wired_once_not_once_per_run():
    """build_profile runs on every iteration; the hook list must not grow."""
    profile = {"permissions": {}}
    autoloop_git.add_session_guard(profile)
    autoloop_git.add_session_guard(profile)

    assert len(profile["hooks"]["PreToolUse"]) == 1


def test_existing_hooks_in_the_base_profile_survive():
    profile = {"hooks": {"PreToolUse": [{"matcher": "Write", "hooks": []}]}}
    autoloop_git.add_session_guard(profile)

    assert len(profile["hooks"]["PreToolUse"]) == 2
    assert profile["hooks"]["PreToolUse"][0]["matcher"] == "Write"


# --- the run's own handoff ------------------------------------------------


def test_handoff_lives_in_the_run_journal_not_the_session(project_dir):
    """AC 3: the note an iteration leaves must not touch the session row."""
    before = session_rows(project_dir)

    handoff.write(str(project_dir), "сделал X, дальше Y", task_slug="my-task")

    assert session_rows(project_dir) == before
    assert handoff.last_text(str(project_dir)) == "сделал X, дальше Y"


def test_latest_handoff_wins_and_earlier_ones_stay_readable(project_dir):
    handoff.write(str(project_dir), "первая", task_slug="a")
    handoff.write(str(project_dir), "вторая", task_slug="b")

    assert handoff.last_text(str(project_dir)) == "вторая"
    assert len(journal.read_events(str(project_dir), journal.EVENT_HANDOFF)) == 2


def test_empty_handoff_is_refused_not_recorded(project_dir):
    assert handoff.write(str(project_dir), "   ") is False
    assert handoff.last_text(str(project_dir)) == ""


def test_handoff_reaches_the_next_prompt(project_dir):
    """Continuity is the supervisor's job, not the agent's memory."""
    text = build_prompt(
        "my-task",
        {"soft_threshold": 30},
        template_path=PROMPT_PATH,
        handoff="упёрся в гейт filesize",
    )

    assert "## Итог предыдущей итерации" in text
    assert "упёрся в гейт filesize" in text


def test_no_handoff_leaves_no_empty_section():
    assert "Итог предыдущей итерации" not in prompt_text()


def test_handoff_events_are_not_mistaken_for_iterations(project_dir):
    """Events share the file with iterations; the report must keep them apart."""
    journal.open_iteration(str(project_dir), 1, "my-task", {})
    handoff.write(str(project_dir), "итог", task_slug="my-task")

    entries = journal.read_entries(str(project_dir))

    assert len(entries) == 1
    assert entries[0]["task_slug"] == "my-task"


# --- AC 3 + 5: the supervisor around a live session -----------------------


@pytest.fixture
def loop_env(monkeypatch, project_dir):
    """A run whose child does nothing but report a session id."""
    monkeypatch.setattr(autoloop, "PROJECT_DIR", Path(project_dir))
    monkeypatch.setattr(autoloop, "tausik_cli", lambda _dir: "fake-tausik")
    state = {"queue": ["task-a"], "on_child": None, "prompts": []}

    def fake_cli(project, args, timeout=30):
        if args[:2] == ["task", "list"]:
            status = args[args.index("--status") + 1]
            if status == "planning" and state["queue"]:
                return "slug title status\n----\ntask-a Title planning\n"
        return ""

    def fake_run(cmd, **kwargs):
        if cmd and cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 1, "", "")
        state["prompts"].append(cmd[2] if len(cmd) > 2 else "")
        state["queue"].clear()
        if state["on_child"]:
            state["on_child"]()
        payload = {"is_error": False, "total_cost_usd": 0.1, "session_id": SESSION_A}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

    monkeypatch.setattr(autoloop, "_run_cli", fake_cli)
    monkeypatch.setattr(subprocess, "run", fake_run)
    return state


def run_loop(**overrides):
    args = argparse.Namespace(
        command="run",
        max_iterations=None,
        max_idle=None,
        max_crashes=None,
        model=None,
        timeout=60,
        dry_run=False,
        git_mode="off",
        direction="",
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return autoloop.loop(args)


def test_a_run_leaves_the_humans_session_and_handoff_untouched(loop_env, project_dir):
    """AC 3: the whole point — the human comes back to what they left."""
    conn = sqlite3.connect(project_dir / ".tausik" / "tausik.db")
    conn.execute("UPDATE sessions SET handoff = 'человек: доделать импорт'")
    conn.commit()
    conn.close()

    run_loop()

    (row,) = session_rows(project_dir)
    assert row[1] is None, "сессия человека закрыта прогоном"
    assert row[2] == "человек: доделать импорт"


def test_run_without_an_open_session_does_not_open_one(
    loop_env, project_dir, close_session
):
    """AC 5: no session is not a problem to fix — the run just works."""
    close_session()
    before = session_rows(project_dir)

    assert run_loop() == 0
    assert session_rows(project_dir) == before
    assert journal.read_events(str(project_dir), journal.EVENT_SESSION_CLOSED) == []


def test_a_session_closed_by_the_iteration_is_journalled(loop_env, project_dir):
    """AC 4: a closure that slipped past the guard still leaves a trace."""
    conn = sqlite3.connect(project_dir / ".tausik" / "tausik.db")
    loop_env["on_child"] = lambda: (
        conn.execute("UPDATE sessions SET ended_at = '2026-08-12T20:00:00Z'"),
        conn.commit(),
    )

    run_loop()
    conn.close()

    (event,) = journal.read_events(str(project_dir), journal.EVENT_SESSION_CLOSED)
    assert event["task_slug"] == "task-a"
    assert "просочилось" in journal.format_report(str(project_dir))


def test_a_session_already_closed_before_the_iteration_is_not_blamed(
    loop_env, project_dir, close_session
):
    """AC negative: the run must not report a closure it did not cause."""
    close_session()

    run_loop()

    assert journal.read_events(str(project_dir), journal.EVENT_SESSION_CLOSED) == []


def test_closure_check_reads_the_session_row_it_started_from(project_dir):
    row_id, ended = read_session_row(str(project_dir))

    assert row_id == 1
    assert ended is None
    assert (
        autoloop.report_session_closure(Path(project_dir), (row_id, ended), "t")
        is False
    )


def test_previous_handoff_is_pasted_into_the_next_iterations_prompt(
    loop_env, project_dir
):
    handoff.write(str(project_dir), "итог прошлой итерации", task_slug="task-a")

    run_loop()

    assert "итог прошлой итерации" in loop_env["prompts"][0]
