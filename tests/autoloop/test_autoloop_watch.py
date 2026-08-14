"""The watcher: when it acts, when it refuses, and whose chat it belongs to."""

import json

import autoloop_chat_cycle as cycle_state
import autoloop_presence as presence
import autoloop_watch as watch
import chat_watch as hook  # hooks/ is on the path via conftest
import pytest


# --- when to act -----------------------------------------------------------


def test_a_full_window_in_a_quiet_chat_is_the_moment():
    assert watch.should_act(35, threshold=30, quiet_for=60) is True


def test_a_full_window_in_a_live_conversation_is_left_alone():
    """Wiping the context of somebody mid-thought is the one unforgivable
    failure of this mechanism."""
    assert watch.should_act(35, threshold=30, quiet_for=5) is False


def test_a_quiet_chat_below_the_threshold_is_left_alone():
    assert watch.should_act(12, threshold=30, quiet_for=600) is False


def test_without_a_measurement_nothing_happens():
    """AC negative: no reading is not a reading of 100%."""
    assert watch.should_act(None, threshold=30, quiet_for=600) is False


# --- what counts as a measurement -----------------------------------------


def test_a_stale_reading_is_not_a_measurement(project_dir):
    readings = project_dir / ".tausik" / "autoloop"
    readings.mkdir(parents=True)
    path = readings / "session.json"
    path.write_text(json.dumps({"percent": 44.0}), encoding="utf-8")
    stamp = path.stat().st_mtime

    assert watch.reading(str(project_dir), now=stamp + 10) == 44.0
    assert watch.reading(str(project_dir), now=stamp + 10_000) is None


def test_files_without_a_percent_are_somebody_elses(project_dir):
    """The loop writes permission profiles nearby; one of them once outranked
    every real reading."""
    readings = project_dir / ".tausik" / "autoloop"
    readings.mkdir(parents=True)
    (readings / "profile.json").write_text('{"permissions": []}', encoding="utf-8")

    assert watch.reading(str(project_dir)) is None


def test_no_transcript_reads_as_unknown(tmp_path):
    """AC negative: not finding the conversation is not evidence that nobody is
    in it. This used to answer "quiet forever", and that answer is what let the
    watcher arm itself over somebody who was typing."""
    assert watch.idle_seconds(None) is None
    assert watch.idle_seconds(str(tmp_path / "missing.jsonl")) is None


def test_an_unknown_quiet_never_acts():
    """AC negative: the full window is real, the empty room is a guess."""
    assert watch.should_act(99, threshold=30, quiet_for=None) is False


def test_a_fresh_transcript_reads_as_busy(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text("{}", encoding="utf-8")

    assert watch.idle_seconds(str(path), now=path.stat().st_mtime + 3) == pytest.approx(3, abs=1)


# --- the sequence ----------------------------------------------------------


class Recorder:
    def __init__(self, ok=True):
        self.sent = []
        self.ok = ok

    def send(self, pid, text, submit=True):
        self.sent.append((text, submit))
        return self.ok, ""

    @property
    def typed(self):
        return [text for text, _ in self.sent]


@pytest.fixture
def keyboard(project_dir, monkeypatch):
    """A chat that accepts every key, confirms every command, and costs no
    wall-clock. Individual tests take away whichever of those they are about."""
    rec = Recorder()
    monkeypatch.setattr(watch.keys, "send_to_console", rec.send)
    monkeypatch.setattr(watch, "wait_ready", lambda *_a, **_k: True)
    monkeypatch.setattr(watch, "confirm", lambda *_a, **_k: True)
    monkeypatch.setattr(watch.time, "sleep", lambda _s: None)
    monkeypatch.setattr(watch, "session_spent", lambda _p: None)
    return rec


def test_the_draft_is_cleared_before_each_command(project_dir, keyboard):
    """Without Esc the command glues itself onto whatever the human had
    half-typed, and both are ruined."""
    watch.run_sequence(str(project_dir), 111, watch.Maintenance())

    assert keyboard.typed == [
        watch.ESC,
        "/checkpoint",
        watch.ESC,
        "/clear",
        watch.ESC,
        "/start",
    ]
    assert (watch.ESC, False) in keyboard.sent  # Esc is typed, never submitted


def test_nothing_is_typed_while_a_turn_is_running(project_dir, keyboard, monkeypatch):
    """An Enter sent during a turn can answer an open permission dialog."""
    monkeypatch.setattr(watch, "wait_ready", lambda *_a, **_k: False)

    assert watch.run_sequence(str(project_dir), 111, watch.Maintenance()) is False
    assert keyboard.typed == []


def test_clear_never_precedes_a_finished_checkpoint(project_dir, keyboard, monkeypatch):
    """AC negative: the handoff in the database is the only other copy of the
    conversation about to be erased."""
    monkeypatch.setattr(watch, "confirm", lambda *_a, **_k: False)

    ok = watch.run_sequence(str(project_dir), 111, watch.Maintenance())

    assert ok is False
    assert "/clear" not in keyboard.typed


def test_a_command_that_left_no_trace_is_not_reported_as_done(project_dir, keyboard, monkeypatch):
    """The live failure: `/start` was written into a console that had stopped
    reading, the write succeeded, and the log said "подано" for a command that
    never ran."""
    monkeypatch.setattr(watch, "confirm", lambda *_a, **_k: False)

    watch.run_sequence(str(project_dir), 111, watch.Maintenance())

    journal = (project_dir / ".tausik" / "chat-watch.log").read_text(encoding="utf-8")
    assert "выполнен" not in journal
    assert "следа нет" in journal


def test_a_command_lost_on_the_way_is_typed_again(project_dir, keyboard, monkeypatch):
    """Right after `/clear` the chat is rebuilding itself and reads nothing;
    one retry is the difference between a finished cycle and a silent stall."""
    answers = [False, True, True, True]
    monkeypatch.setattr(watch, "confirm", lambda *_a, **_k: answers.pop(0))

    ok = watch.run_sequence(str(project_dir), 111, watch.Maintenance())

    assert ok is True
    assert keyboard.typed.count("/checkpoint") == 2


def test_a_command_nobody_answers_gives_up_after_a_few_tries(project_dir, keyboard, monkeypatch):
    monkeypatch.setattr(watch, "confirm", lambda *_a, **_k: False)

    watch.run_sequence(str(project_dir), 111, watch.Maintenance())

    assert keyboard.typed.count("/checkpoint") == watch.DELIVERY_ATTEMPTS


def test_a_console_that_refuses_stops_the_sequence(project_dir, keyboard):
    keyboard.ok = False

    ok = watch.run_sequence(str(project_dir), 111, watch.Maintenance())

    assert ok is False
    assert "/clear" not in keyboard.typed


def test_a_finished_run_does_not_re_offer_on_the_same_reading(project_dir, keyboard):
    cycle = watch.Maintenance(threshold=30)

    watch.run_sequence(str(project_dir), 111, cycle)

    assert cycle.consider(40, now=999) is False


# --- closing the session, but only when it is actually spent --------------


def status_saying(monkeypatch, text):
    class Result:
        stdout = text

    monkeypatch.setattr(watch.subprocess, "run", lambda *_a, **_k: Result())


ROOMY = (
    "Tasks: 1/2 done\n"
    "Session: #8 (active 12m / 180m)\n"
    "Capacity: 30/200 used, 40 planned, 130 remaining\n"
)


def test_a_session_with_room_left_is_not_closed(project_dir, keyboard):
    """A full window says nothing about either budget. Closing a session early
    throws away the history the metrics are counted from."""
    watch.run_sequence(str(project_dir), 111, watch.Maintenance())

    assert "/end" not in keyboard.typed


def test_a_spent_session_is_closed_before_the_wipe(project_dir, keyboard, monkeypatch):
    """The handoff has to be written while the conversation it summarises still
    exists — after `/clear` there is nothing left to summarise."""
    monkeypatch.setattr(watch, "session_spent", lambda _p: "время 181/180 мин")

    watch.run_sequence(str(project_dir), 111, watch.Maintenance())

    assert keyboard.typed == [
        watch.ESC,
        "/checkpoint",
        watch.ESC,
        "/end",
        watch.ESC,
        "/clear",
        watch.ESC,
        "/start",
    ]


def test_an_unreadable_status_leaves_the_session_alone(project_dir, keyboard, monkeypatch):
    """AC negative: not knowing what is left is not the same as having nothing
    left."""
    monkeypatch.setattr(watch, "status_text", lambda _p: None)

    watch.run_sequence(str(project_dir), 111, watch.Maintenance())

    assert "/end" not in keyboard.typed


def test_a_session_out_of_time_is_spent(project_dir, monkeypatch):
    status_saying(monkeypatch, ROOMY.replace("active 12m", "active 180m"))

    assert "время" in watch.session_spent(str(project_dir))


def test_a_session_out_of_calls_is_spent(project_dir, monkeypatch):
    """The gate that actually refuses work: `task start` blocks on remaining
    calls long before the 180 minutes are up — a chat cleaned but left in a
    session that cannot take a task is a chat that can only talk."""
    status_saying(
        monkeypatch,
        ROOMY.replace("30/200 used, 40 planned, 130", "200/200 used, 0 planned, 0"),
    )

    assert "ёмкость" in watch.session_spent(str(project_dir))


def test_a_session_whose_capacity_is_all_booked_is_spent(project_dir, monkeypatch):
    """Used calls are few, but everything left is promised to planned tasks —
    the next `task start` is refused all the same."""
    status_saying(
        monkeypatch,
        ROOMY.replace("30/200 used, 40 planned, 130", "57/200 used, 143 planned, 0"),
    )

    assert "ёмкость" in watch.session_spent(str(project_dir))


def test_a_session_with_both_budgets_intact_is_not_spent(project_dir, monkeypatch):
    status_saying(monkeypatch, ROOMY)

    assert watch.session_spent(str(project_dir)) is None


def test_a_status_without_the_budget_lines_is_not_a_verdict(project_dir, monkeypatch):
    status_saying(monkeypatch, "Tasks: 1/2 done\n")

    assert watch.session_spent(str(project_dir)) is None


# --- one watcher, and only for its own chat -------------------------------


def test_a_second_watcher_does_not_start(project_dir, monkeypatch):
    monkeypatch.setattr(watch.keys, "pid_exists", lambda _pid: True)
    assert watch.take_lock(str(project_dir)) is True

    assert watch.take_lock(str(project_dir)) is False


def test_a_lock_from_a_dead_watcher_is_taken_over(project_dir, monkeypatch):
    monkeypatch.setattr(watch.keys, "pid_exists", lambda _pid: True)
    watch.take_lock(str(project_dir))
    monkeypatch.setattr(watch.keys, "pid_exists", lambda _pid: False)

    assert watch.take_lock(str(project_dir)) is True


def test_the_hook_finds_the_chat_that_started_it():
    """Not "the only claude.exe running" — there may be several, and typing
    into the wrong one is the failure this must never have."""
    table = {
        50: (40, "python.exe"),  # the hook
        40: (30, "claude.exe"),  # its chat
        30: (20, "windowsterminal.exe"),
    }

    assert hook.owning_chat(50, table) == 40


def test_the_hook_gives_up_rather_than_guess():
    table = {50: (40, "python.exe"), 40: (30, "cmd.exe"), 30: (0, "explorer.exe")}

    assert hook.owning_chat(50, table) is None


def test_the_hook_records_that_a_session_came_up(project_dir):
    """`/clear` ends no turn, so this mark is the only proof it landed."""
    hook.mark_started(str(project_dir))

    assert (project_dir / ".tausik" / ".chat.started").exists()


# --- following the conversation across a wipe ------------------------------


def test_the_watcher_follows_the_session_it_was_given(project_dir, tmp_path):
    known = tmp_path / "known.jsonl"
    known.write_text("{}", encoding="utf-8")

    assert watch.transcript_path(str(project_dir), str(known)) == str(known)


def test_after_a_wipe_the_watcher_follows_the_new_conversation(project_dir, tmp_path):
    """The wipe starts a new session in a new file. A watcher left on the old
    one sees a chat that never speaks — and a chat that never speaks is one it
    considers safe to wipe again, mid-sentence."""
    old, new = tmp_path / "old.jsonl", tmp_path / "new.jsonl"
    old.write_text("{}", encoding="utf-8")
    new.write_text("{}", encoding="utf-8")
    hook.mark_started(str(project_dir), str(new))

    assert watch.transcript_path(str(project_dir), str(old)) == str(new)


def test_a_session_whose_file_does_not_exist_yet_is_still_the_session(project_dir, tmp_path):
    """Claude Code creates the transcript on the first message, so a fresh
    session is named minutes before its file appears. Falling back to the
    previous file there is how a watcher ends up measuring the silence of a
    conversation that has already been replaced."""
    old = tmp_path / "old.jsonl"
    old.write_text("{}", encoding="utf-8")
    unborn = tmp_path / "not-yet.jsonl"
    hook.mark_started(str(project_dir), str(unborn))

    assert watch.transcript_path(str(project_dir), str(old)) == str(unborn)


def test_a_fresh_session_is_not_wiped_before_it_has_spoken(project_dir, tmp_path):
    """AC negative: the live incident. The transcript does not exist yet, so
    the chat looks silent — and a silent chat with a full window is exactly
    what this mechanism types into."""
    hook.mark_started(str(project_dir), str(tmp_path / "not-yet.jsonl"))

    quiet = watch.idle_seconds(watch.transcript_path(str(project_dir)))

    assert quiet is None
    assert watch.should_act(99, threshold=30, quiet_for=quiet) is False


# --- finding the folder at all ---------------------------------------------


def test_the_slug_is_built_the_way_claude_names_the_folder():
    """The defect that switched off the "somebody is typing" check entirely:
    `_` was left alone, so the search asked for a folder that does not exist."""
    assert presence.project_slug(r"D:\Claude_mcp") == "D--Claude-mcp"
    assert "_" not in presence.project_slug(r"D:\one_two\three.four")


def test_a_project_whose_name_contains_ours_is_not_ours(tmp_path, monkeypatch):
    """AC negative: scratchpad projects created inside a project carry its whole
    slug in their own names. A `*slug*` match reaches them, and then "is anyone
    talking?" is answered about a different chat."""
    projects = tmp_path / ".claude" / "projects"
    ours = projects / "D--Claude-mcp"
    theirs = projects / "C--Temp-D--Claude-mcp-scratchpad"
    for folder in (ours, theirs):
        folder.mkdir(parents=True)
    (ours / "mine.jsonl").write_text("{}", encoding="utf-8")
    (theirs / "stranger.jsonl").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(presence.os.path, "expanduser", lambda p: p.replace("~", str(tmp_path), 1))
    monkeypatch.setattr(presence, "project_slug", lambda _d: "D--Claude-mcp")

    found = presence.newest_transcript(presence.transcript_dir("ignored"))

    assert found == str(ours / "mine.jsonl")


def test_a_project_with_no_transcript_folder_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(presence.os.path, "expanduser", lambda p: p.replace("~", str(tmp_path), 1))

    assert presence.transcript_dir(str(tmp_path / "nowhere")) is None


# --- the last look before typing -------------------------------------------


def spin_watch(monkeypatch, project_dir, screens, ticks=3, quiet=600.0):
    """Run the loop for a few ticks with everything but the draft check fixed:
    the window is full, the transcript is quiet, the chat is alive."""
    clock = iter([0.0, 10.0, 20.0, 30.0])
    beats = iter([True] * ticks + [False])
    ran = []
    cycle_state.start_run(str(project_dir), "очередь задач")
    monkeypatch.setattr(watch.keys, "pid_exists", lambda _pid: True)
    monkeypatch.setattr(watch, "alive", lambda _pid: next(beats))
    monkeypatch.setattr(watch, "reading", lambda _p: 99)
    monkeypatch.setattr(watch, "transcript_path", lambda *_a: "chat.jsonl")
    monkeypatch.setattr(watch, "idle_seconds", lambda *_a, **_k: quiet)
    monkeypatch.setattr(watch.keys, "console_text", lambda _pid: next(screens))
    monkeypatch.setattr(watch.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(watch.time, "sleep", lambda _s: None)
    monkeypatch.setattr(watch, "run_sequence", lambda *_a: ran.append("sequence") or True)
    watch.watch(str(project_dir), pid=111, threshold=30)
    return ran, (project_dir / ".tausik" / "chat-watch.log").read_text(encoding="utf-8")


def test_a_draft_typed_during_the_countdown_stops_the_wipe(project_dir, monkeypatch):
    """AC negative: the input line is the one place the transcript cannot see.
    Fifteen seconds of "silence" can be fifteen seconds of typing."""
    ran, journal = spin_watch(monkeypatch, project_dir, iter(["> прив", "> привет"]))

    assert ran == []
    assert "человек печатает" in journal


def test_a_still_screen_lets_the_cleanup_through(project_dir, monkeypatch):
    """The guard must not become a permanent refusal — a chat nobody touched
    still gets cleaned."""
    ran, _journal = spin_watch(monkeypatch, project_dir, iter(["> ", "> "]))

    assert ran == ["sequence"]


def test_a_watcher_that_cannot_see_the_transcript_says_so(project_dir, monkeypatch):
    """Going blind is the state in which this mechanism does nothing at all —
    and a silent watcher that never acts looks exactly like a working one."""
    ran, journal = spin_watch(monkeypatch, project_dir, iter(["> ", "> "]), quiet=None)

    assert ran == []
    assert "считаю, что человек в чате" in journal


# --- the pipe the host is still waiting on ---------------------------------


def spawned_with(monkeypatch, project_dir):
    seen = {}
    monkeypatch.setattr(watch.subprocess, "Popen", lambda _cmd, **kw: seen.update(kw))
    watch.spawn(str(project_dir), 111)
    return seen


def test_the_watcher_never_inherits_the_hooks_pipes(project_dir, monkeypatch):
    """AC negative: the host reads a hook's output through a pipe and waits for
    EOF. An unredirected Popen hands the detached child a copy of that pipe's
    write end, and a watcher living for hours never lets it close — the hook
    has exited, the host is still waiting, and the human's input line takes
    nothing. Measured: EOF never arrived in 8 s unredirected, immediately once
    redirected. Two live sessions were lost to this before it was found."""
    seen = spawned_with(monkeypatch, project_dir)

    assert seen["stdin"] == watch.subprocess.DEVNULL
    assert seen["stdout"] == watch.subprocess.DEVNULL
    assert seen.get("stderr") is not None  # redirected somewhere, never inherited


def test_a_watcher_that_dies_leaves_a_trace(project_dir, monkeypatch):
    """Its own log line is written by code that got to run; a process killed by
    an exception writes nothing there at all."""
    seen = spawned_with(monkeypatch, project_dir)

    assert seen["stderr"] != watch.subprocess.DEVNULL
    assert (project_dir / ".tausik" / "chat-watch.err.log").exists()


def test_a_watcher_leaves_when_the_run_is_taken_away(project_dir, monkeypatch):
    """AC: stop is a file being removed, not a signal — nothing to send and no
    PID to get wrong. The watcher notices within a couple of seconds."""
    monkeypatch.setattr(watch.keys, "pid_exists", lambda _pid: True)
    monkeypatch.setattr(watch, "alive", lambda _pid: True)
    ran = []
    monkeypatch.setattr(watch, "run_sequence", lambda *_a: ran.append("sequence") or True)

    watch.watch(str(project_dir), pid=111, threshold=30)

    journal = (project_dir / ".tausik" / "chat-watch.log").read_text(encoding="utf-8")
    assert ran == []
    assert "прогон снят" in journal
