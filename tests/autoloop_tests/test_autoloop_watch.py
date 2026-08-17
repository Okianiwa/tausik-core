"""The watcher: when it acts, when it refuses, and whose chat it belongs to."""

import json
import os
import time

import autoloop_chat_cycle as cycle_state
import autoloop_presence as presence
import autoloop_run_state as run_state
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


class TestWorkStillRunningIsNotSilence:
    """A quiet transcript means the TURN ended, not the work. An agent waiting
    on a background command it started writes nothing for minutes; the cleanup
    used to land in the middle of that, at a live task, with the result unread.
    Caught on the v1.8.0 migration: a full test run of 7665 tests, 18 minutes
    of silence, window past the soft threshold."""

    def test_a_running_job_holds_the_cleanup_off(self):
        assert watch.should_act(35, threshold=30, quiet_for=600, busy=True, hard=75) is False

    def test_the_same_moment_acts_once_the_job_is_gone(self):
        assert watch.should_act(35, threshold=30, quiet_for=600, busy=False, hard=75) is True

    def test_waiting_is_bounded_by_the_hard_threshold(self):
        """NEGATIVE: a job that never exits must not silence the watcher for
        good — past the hard fill the window is worth more than the wait."""
        assert watch.should_act(80, threshold=30, quiet_for=600, busy=True, hard=75) is True

    def test_a_live_conversation_still_wins_over_everything(self):
        """Busy or not, a person typing is never interrupted."""
        assert watch.should_act(80, threshold=30, quiet_for=5, busy=False, hard=75) is False

    def test_descendants_are_found_through_intermediate_shells(self):
        """`chat -> shell -> python`: a direct-children check would call this quiet."""
        table = {100: (1, "claude.exe"), 200: (100, "bash.exe"), 300: (200, "python.exe")}

        assert presence.descendants(100, table) == {200, 300}

    def test_the_mechanism_is_not_its_own_background_work(self, project_dir):
        """The watcher and the overlay hang off the same chat and outlive every
        turn. Counted as work, they would mean the window is never cleaned."""
        table = {100: (1, "claude.exe"), 200: (100, "python.exe"), 300: (100, "python.exe")}
        presence.register_own(str(project_dir), 200)

        assert presence.background_pids(100, table, presence.own_pids(str(project_dir))) == {300}

    def test_what_came_up_with_the_chat_is_not_work(self):
        """Measured on a live chat: 43 descendants, every MCP server aged
        exactly as the chat itself (143 min), one background command aged 0.
        Counting the furniture as work means the window is never cleaned."""
        table = {100: (1, "claude.exe"), 200: (100, "serena.exe"), 300: (100, "python.exe")}
        ages = {100: 1000.0, 200: 1001.0, 300: 1500.0}  # server with the chat, job later

        work = presence.background_pids(100, table, age_of=ages.get, grace=120)

        assert work == {300}

    def test_an_unreadable_age_does_not_block_the_cleanup(self):
        """A process that cannot be asked is not proven to be work — blocking
        forever on something unreadable is the worse failure."""
        table = {100: (1, "claude.exe"), 200: (100, "python.exe")}
        ages = {100: 1000.0}  # 200 answers None

        assert presence.background_pids(100, table, age_of=ages.get, grace=120) == set()

    def test_an_unknown_chat_age_counts_everything(self):
        """The other direction of the same caution: with no baseline to compare
        against, the watcher waits rather than cleans."""
        table = {100: (1, "claude.exe"), 200: (100, "python.exe")}

        assert presence.background_pids(100, table, age_of=lambda _pid: None) == {200}

    def test_a_process_that_only_lives_is_not_work(self):
        """The defect this split answers: serena starts Eclipse JDT LS lazily,
        on the first Java symbol lookup, so it is born long after the boot
        grace and counted as work for as long as the MCP server lives.
        Measured on a live chat — the tree sat at 4 processes for 98 minutes
        and chat-watch.log never logged "работа кончилась"."""
        cpu = {200: 5.0}  # same reading on both ticks

        _, first = presence.busy_pids({200}, None, cpu_of=cpu.get)
        working, _ = presence.busy_pids({200}, first, cpu_of=cpu.get)

        assert working == set()

    def test_cpu_growth_between_ticks_is_work(self):
        """The other half of the pair. Measured over the watcher's own 2s tick:
        idle reads 0 ms, a working process 344 ms — nothing to tune between."""
        readings = iter([5.0, 5.344])

        _, first = presence.busy_pids({200}, None, cpu_of=lambda _pid: next(readings))
        working, _ = presence.busy_pids({200}, first, cpu_of=lambda _pid: next(readings))

        assert working == {200}

    def test_a_first_sighting_proves_nothing_yet(self):
        """No baseline means no evidence — one tick later there is. Short-lived
        shells are covered by the transcript clock, not by this counter."""
        working, snapshot = presence.busy_pids({200}, None, cpu_of=lambda _pid: 5.0)

        assert working == set()
        assert snapshot == {200: 5.0}

    def test_work_launched_through_an_mcp_server_still_counts(self):
        """NEGATIVE: the rejected fix was to prune every subtree hanging off a
        boot-era process. It would have cleared the language server AND blinded
        the counter to `mcp__windows-mcp__PowerShell`, which starts real work
        under its own server. Parentage must not decide; CPU must."""
        table = {100: (1, "claude.exe"), 200: (100, "mcp.exe"), 300: (200, "powershell.exe")}
        ages = {100: 1000.0, 200: 1001.0, 300: 1500.0}  # server with the chat, job later
        started = presence.background_pids(100, table, age_of=ages.get, grace=120)
        readings = iter([1.0, 1.5])

        _, first = presence.busy_pids(started, None, cpu_of=lambda _pid: next(readings))
        working, _ = presence.busy_pids(started, first, cpu_of=lambda _pid: next(readings))

        assert started == {300}
        assert working == {300}

    def test_an_unreadable_cpu_does_not_block_the_cleanup(self):
        """NEGATIVE: same direction `started_at` already takes — a process that
        cannot be asked is not proven to be working, and blocking forever on
        something unreadable is the worse failure."""
        working, snapshot = presence.busy_pids({200}, {200: 5.0}, cpu_of=lambda _pid: None)

        assert working == set()
        assert snapshot == {}

    def test_a_dead_registration_does_not_linger(self, project_dir):
        """NEGATIVE: a registry that only grows would eventually name a recycled
        pid and hide real work behind it."""
        presence.register_own(str(project_dir), 200)
        presence.register_own(str(project_dir), 300)

        alive = presence.own_pids(str(project_dir), is_alive=lambda pid: pid == 300)

        assert alive == {300}

    def test_an_unwritable_registry_is_not_fatal(self, project_dir):
        """A registry that cannot be read answers "nothing of ours" rather than
        raising: a delayed cleanup beats a dead watcher."""
        (project_dir / ".tausik" / ".autoloop-own.json").write_text("{не json", encoding="utf-8")

        assert presence.own_pids(str(project_dir)) == set()


class TestAHeartbeatIsNotWork:
    """Measured in D:/asynchronus on 17.08.2026: the graph MCP server respawns
    `codebase-memory-mcp cli --index-worker` every 54s and it burns 13s of CPU,
    leaving 41s of silence against the 45s a cleanup needs. chat-watch.log shows
    the whole evening as "фоновая работа: 1 процессов" -> "кончилась" -> again,
    and the window sat between 30% and 75% without one cleanup. Both older
    exclusions pass it: it is born fresh every time, and it really is busy."""

    PERIOD, WORKING, FIRST_WAKE = 54.0, 13.0, 41.0  # from the log, in seconds
    BOOT, QUIET_FROM = 1000.0, 4600.0  # chat born, then an hour later the turn ends
    CHAT, SERVER, WORKER = 100, 200, 300
    TABLE = {
        CHAT: (1, "claude.exe"),
        SERVER: (CHAT, "codebase-memory-mcp.exe"),
        WORKER: (SERVER, "codebase-memory-mcp.exe"),
    }

    def ages(self, worker_born):
        return {self.CHAT: self.BOOT, self.SERVER: self.BOOT + 1, self.WORKER: worker_born}.get

    def selected(self, worker_born, quiet_since):
        return presence.background_pids(
            self.CHAT,
            self.TABLE,
            age_of=self.ages(worker_born),
            grace=120,
            quiet_since=quiet_since,
        )

    def test_a_process_born_deep_into_the_silence_is_nobody_s_work(self):
        """41 seconds after the last write nobody was there to ask for it."""
        born = self.QUIET_FROM + self.FIRST_WAKE

        assert self.selected(born, self.QUIET_FROM) == set()

    def test_a_process_born_while_the_turn_ran_is_work(self):
        """The other side of the same line: the tool call is written to the
        transcript before the process spawns, so real work is always born on
        this side of it."""
        born = self.QUIET_FROM - 30

        assert self.selected(born, self.QUIET_FROM) == {self.WORKER}

    def test_an_unreadable_transcript_does_not_prune_anything(self):
        """NEGATIVE: with no idea when the turn ended, a birth time proves
        nothing. Pruning on a guess would clean over a live agent; refusing
        costs one window, and the watcher will not clean while blind anyway."""
        born = self.QUIET_FROM + self.FIRST_WAKE

        assert self.selected(born, None) == {self.WORKER}

    def test_a_child_of_the_agents_own_job_still_counts(self):
        """NEGATIVE: `gradlew build` started in the turn spawns its daemon well
        into the silence. The daemon inherits legitimacy through its parent —
        otherwise the cleanup lands in the middle of a build."""
        table = {100: (1, "claude.exe"), 200: (100, "bash.exe"), 300: (200, "java.exe")}
        ages = {100: self.BOOT, 200: self.QUIET_FROM - 20, 300: self.QUIET_FROM + 30}

        work = presence.background_pids(
            100, table, age_of=ages.get, grace=120, quiet_since=self.QUIET_FROM
        )

        assert work == {200, 300}

    def test_an_unreadable_age_is_still_not_work(self):
        """NEGATIVE: the direction `started_at` already takes must survive the
        new filter — blocking forever on something unreadable is worse than a
        cleanup that happens."""
        table = {100: (1, "claude.exe"), 200: (100, "python.exe")}

        work = presence.background_pids(
            100, table, age_of={100: self.BOOT}.get, grace=120, quiet_since=self.QUIET_FROM
        )

        assert work == set()

    def cadence(self, quiet_since):
        """Five minutes of 2s ticks over the measured cadence.

        Scaffolding replicates only the ORDER of the watcher's tick; both
        decisions in it are the production ones. `busy` skips `busy_pids`
        because the measurement settled that question — the worker burns 13s of
        real CPU, so the CPU check counts it whenever it is selected at all.
        """
        busy_since, quietest, acted = None, 0.0, None
        for tick in range(0, 300, 2):
            now = self.QUIET_FROM + tick
            since_wake = (tick - self.FIRST_WAKE) % self.PERIOD if tick >= self.FIRST_WAKE else None
            alive = since_wake is not None and since_wake < self.WORKING
            table = self.TABLE if alive else {
                pid: entry for pid, entry in self.TABLE.items() if pid != self.WORKER
            }
            work = presence.background_pids(
                self.CHAT,
                table,
                age_of=self.ages(now - since_wake if alive else None),
                grace=120,
                quiet_since=quiet_since,
            )
            busy = bool(work)
            if busy:
                busy_since = now
            quiet = watch.quiet_after_work(float(tick), now, busy_since)
            quietest = max(quietest, quiet)
            if acted is None and watch.should_act(35, 30, quiet, busy=busy, hard=75):
                acted = tick
        return acted, quietest

    def test_the_defect_it_answers_the_cleanup_could_never_come(self):
        """NEGATIVE, and the reason this class exists: on the old selection the
        silence has a ceiling of one gap between wake-ups. 41s < 45s, so the
        answer is never — not slow, never."""
        acted, quietest = self.cadence(quiet_since=None)

        assert acted is None
        assert quietest == pytest.approx(self.FIRST_WAKE, abs=2)

    def test_the_cleanup_happens_on_the_measured_cadence(self):
        """The fix, on the numbers from the log: with the heartbeat out of the
        count nothing resets the clock, so the silence grows past 45s."""
        acted, quietest = self.cadence(quiet_since=self.QUIET_FROM)

        assert acted is not None and acted <= 46
        assert quietest > watch.IDLE_SECONDS


class TestTheWaitIsNamed:
    """«1 проц.» sent the human hunting for an agent that did not exist, twice
    in one run, while the answer was the graph server's own index worker."""

    def test_the_lowest_pid_names_the_wait(self):
        table = {300: (200, "java.exe"), 400: (200, "codebase-memory-mcp.exe")}

        assert presence.worker_name({400, 300}, table) == "java"

    def test_the_extension_is_noise(self):
        assert presence.worker_name({400}, {400: (200, "codebase-memory-mcp.exe")}) == "codebase-memory-mcp"

    def test_no_work_has_no_name(self):
        assert presence.worker_name(set(), {400: (200, "python.exe")}) == ""

    def test_a_pid_missing_from_the_table_has_no_name(self):
        """NEGATIVE: the snapshot and the busy set are taken a moment apart, so
        a process can die in between. That is a nameless wait, not a crash in
        the watcher's loop."""
        assert presence.worker_name({999}, {400: (200, "python.exe")}) == ""


class TestTheAnchor:
    """The other half of the same defect: rules fade out of a context nobody
    refreshed. A run that stalled between steps waits for a human who is away —
    that is why the run was declared. The nudge re-reads nothing and frees no
    window; it hands the direction back."""

    LONG = watch.ANCHOR_SECONDS + 1
    ENDED, NOW = 5000.0, 5000.0 + watch.ANCHOR_SECONDS + 1

    def due(self, **over):
        args = {"busy": False, "arming": False, "wasted": 0}
        args.update(over)
        return watch.anchor_due(self.LONG, 0.0, self.LONG, **args)

    def standing(self, last_write):
        return watch.standing_seconds(self.ENDED, last_write, self.NOW)

    def test_a_stalled_run_gets_its_direction_back(self):
        """NEGATIVE for the fix below: hardening the idleness test must not turn
        the anchor off. The turn ended, nothing was written after it, and the
        interval has passed."""
        standing = self.standing(self.ENDED - 1)

        assert standing == pytest.approx(watch.ANCHOR_SECONDS + 1)
        assert watch.anchor_due(self.LONG, 0.0, standing, busy=False, arming=False, wasted=0)

    def test_a_running_turn_is_not_a_standing_run(self):
        """The defect this class was rewritten for. Measured on a live run at
        19:15:32: the agent was 20 minutes into `gameprobe-run.sh`, the
        transcript had not moved for all of it, and the first version read that
        as an empty room. The host queued the delivery, which is the only reason
        it cost nothing. A write AFTER the turn ended means a turn is running,
        however quiet it looks."""
        assert self.standing(self.ENDED + 60) is None

    def test_no_word_from_the_host_means_no_nudge(self):
        """NEGATIVE: without the Stop hook's flag there is no evidence the turn
        ended — the transcript alone cannot say. Silence beats guessing."""
        assert watch.standing_seconds(None, self.ENDED, self.NOW) is None

    def test_an_unreadable_transcript_means_no_nudge(self):
        """NEGATIVE: the same answer `should_act` gives — an unreadable chat is
        not an empty room."""
        assert watch.standing_seconds(self.ENDED, None, self.NOW) is None

    def test_a_hook_writing_just_after_the_turn_still_counts_as_standing(self):
        """The tolerance the slack exists for: hooks append their own lines a
        moment after Stop. What it must never swallow is a long tool call, which
        is minutes — see the test above."""
        assert self.standing(self.ENDED + watch.TURN_END_SLACK - 1) is not None

    def test_nothing_happens_before_the_interval(self):
        assert watch.anchor_due(60.0, 0.0, 60.0, busy=False, arming=False, wasted=0) is False

    def test_a_working_run_is_not_nudged(self):
        """NEGATIVE: a job the agent started may run long after its turn ended.
        A nudge there lands on work in flight."""
        assert self.due(busy=True) is False

    def test_an_armed_cleanup_outranks_the_nudge(self):
        """NEGATIVE: the cleanup re-anchors AND frees the window, which is
        strictly more. Two commands typed into the same countdown is the bug
        this ordering avoids."""
        assert self.due(arming=True) is False

    def test_three_nudges_that_moved_nothing_end_it(self):
        """NEGATIVE: silence can mean finished, not stalled. A mechanism typing
        into an empty queue until morning is worse than one that stops."""
        assert self.due(wasted=watch.ANCHOR_TRIES) is False
        assert self.due(wasted=watch.ANCHOR_TRIES - 1) is True

    def test_a_failed_delivery_does_not_retry_every_tick(self):
        """The floor `last_at` keeps: a delivery that failed leaves the host's
        flag untouched, so the standing time alone would fire again in two
        seconds."""
        assert watch.anchor_due(
            10.0, 0.0, self.LONG, busy=False, arming=False, wasted=0, gap=watch.ANCHOR_SECONDS
        ) is False


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


CONTINUATION = "Продолжай прогон. Направление: прогон объявлен"


def test_the_draft_is_cleared_before_each_command(project_dir, keyboard):
    """Without Esc the command glues itself onto whatever the human had
    half-typed, and both are ruined."""
    cycle_state.start_run(str(project_dir), "прогон объявлен")
    watch.run_sequence(str(project_dir), 111, watch.Maintenance())

    assert keyboard.typed == [
        watch.ESC,
        "/checkpoint",
        watch.ESC,
        "/clear",
        watch.ESC,
        "/start",
        # The declared run hands the work back; that step takes no Esc — see
        # test_the_continuation_does_not_press_escape_first.
        CONTINUATION,
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
    cycle_state.start_run(str(project_dir), "прогон объявлен")
    monkeypatch.setattr(watch, "confirm", lambda *_a, **_k: False)

    watch.run_sequence(str(project_dir), 111, watch.Maintenance())

    journal = (project_dir / ".tausik" / "chat-watch.log").read_text(encoding="utf-8")
    assert "выполнен" not in journal
    assert "следа нет" in journal


def test_a_command_lost_on_the_way_is_typed_again(project_dir, keyboard, monkeypatch):
    """Right after `/clear` the chat is rebuilding itself and reads nothing;
    one retry is the difference between a finished cycle and a silent stall."""
    cycle_state.start_run(str(project_dir), "прогон объявлен")
    answers = [False, True, True, True, True]
    monkeypatch.setattr(watch, "confirm", lambda *_a, **_k: answers.pop(0))

    ok = watch.run_sequence(str(project_dir), 111, watch.Maintenance())

    assert ok is True
    assert keyboard.typed.count("/checkpoint") == 2


def test_a_command_nobody_answers_gives_up_after_a_few_tries(project_dir, keyboard, monkeypatch):
    cycle_state.start_run(str(project_dir), "прогон объявлен")
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
    cycle_state.start_run(str(project_dir), "прогон объявлен")
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
        CONTINUATION,
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


# --- the pointer that outlived its conversation ----------------------------


def _transcript_folder(tmp_path, monkeypatch):
    """The folder Claude Code keeps THIS project's transcripts in."""
    folder = tmp_path / ".claude" / "projects" / "D--Claude-mcp"
    folder.mkdir(parents=True)
    monkeypatch.setattr(presence.os.path, "expanduser", lambda p: p.replace("~", str(tmp_path), 1))
    monkeypatch.setattr(presence, "project_slug", lambda _d: "D--Claude-mcp")
    return folder


def _transcript(folder, name, seconds_ago=0):
    path = folder / name
    path.write_text("{}", encoding="utf-8")
    if seconds_ago:
        stamp = time.time() - seconds_ago
        os.utime(path, (stamp, stamp))
    return path


def test_a_pointer_left_by_a_previous_session_is_not_followed(project_dir, tmp_path, monkeypatch):
    """The pointer is written by the SessionStart hook, so it is only as fresh
    as the last session start — and a run is normally declared MID-session,
    after a restart. The window then reported the ended conversation's fill
    (58.4%) as this one's (22.5%), and the watcher waited for silence on a file
    that will never grow again."""
    folder = _transcript_folder(tmp_path, monkeypatch)
    ended = _transcript(folder, "ended.jsonl", seconds_ago=1800)
    current = _transcript(folder, "current.jsonl")
    hook.mark_started(str(project_dir), str(ended))

    assert watch.transcript_path(str(project_dir)) == str(current)


def test_the_pointer_wins_while_its_conversation_is_the_live_one(
    project_dir, tmp_path, monkeypatch
):
    """NEGATIVE: an older file lying beside it must not pull the watcher off
    the session it was actually given — that is what the pointer is for."""
    folder = _transcript_folder(tmp_path, monkeypatch)
    mine = _transcript(folder, "mine.jsonl")
    _transcript(folder, "someone-else.jsonl", seconds_ago=1800)
    hook.mark_started(str(project_dir), str(mine))

    assert watch.transcript_path(str(project_dir)) == str(mine)


def test_a_named_session_whose_file_is_unborn_survives_a_busy_neighbour(
    project_dir, tmp_path, monkeypatch
):
    """NEGATIVE: a fresh session is named minutes before its file exists, and
    the transcript it replaces is the freshest thing on disk at that moment.
    Judging the pointer by mtime alone would hand the new session the old
    conversation — dead end #19's failure, one step removed."""
    folder = _transcript_folder(tmp_path, monkeypatch)
    (folder / "just-ended.jsonl").write_text("{}", encoding="utf-8")
    unborn = folder / "not-yet.jsonl"
    hook.mark_started(str(project_dir), str(unborn))

    assert watch.transcript_path(str(project_dir)) == str(unborn)


def test_the_session_pointer_is_recorded_without_a_run(project_dir, tmp_path, monkeypatch):
    """The pointer must not depend on a run being declared: the declaration
    comes later, and by then the hook has already run and gone."""
    monkeypatch.setattr(hook, "watch_enabled", lambda _d: False)
    monkeypatch.setattr(hook, "payload", lambda: {"transcript_path": str(tmp_path / "now.jsonl")})
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

    assert hook.main() == 0
    pointer = project_dir / ".tausik" / ".chat.session"
    assert pointer.read_text(encoding="utf-8") == str(tmp_path / "now.jsonl")
    assert not (project_dir / ".tausik" / ".chat.started").exists(), (
        "the pid mark is a signal the watcher consumes — outside a run nobody is waiting for it"
    )


# --- one source for the number ---------------------------------------------


def _transcript_with(folder, tokens):
    path = folder / "live.jsonl"
    path.write_text(
        json.dumps({"message": {"model": "claude-opus-5", "usage": {"input_tokens": tokens}}}),
        encoding="utf-8",
    )
    return path


def test_the_live_transcript_outranks_a_stored_reading(project_dir, tmp_path, monkeypatch):
    """The stored reading is written once per turn, and a turn can run for an
    hour. The watcher decides on the same figure the window shows, or it arms a
    cleanup against a number the human is not looking at."""
    folder = _transcript_folder(tmp_path, monkeypatch)
    hook.mark_started(str(project_dir), str(_transcript_with(folder, 120_000)))
    readings = project_dir / ".tausik" / "autoloop"
    readings.mkdir(parents=True, exist_ok=True)
    (readings / "old-session.json").write_text(json.dumps({"percent": 99.0}), encoding="utf-8")
    run_state._live_percent_cache.clear()

    assert run_state.current_percent(str(project_dir)) == 12.0


def test_an_unreadable_transcript_falls_back_to_the_stored_reading(
    project_dir, tmp_path, monkeypatch
):
    """NEGATIVE: losing the transcript must not silently mean "0% full" — the
    last measurement is still the best answer available."""
    _transcript_folder(tmp_path, monkeypatch)
    hook.mark_started(str(project_dir), str(tmp_path / "gone.jsonl"))
    readings = project_dir / ".tausik" / "autoloop"
    readings.mkdir(parents=True, exist_ok=True)
    (readings / "recent.json").write_text(json.dumps({"percent": 44.0}), encoding="utf-8")
    run_state._live_percent_cache.clear()

    assert run_state.current_percent(str(project_dir)) == 44.0


def test_nothing_readable_at_all_is_not_a_reading_of_zero(project_dir, tmp_path, monkeypatch):
    """NEGATIVE: no transcript and no stored reading is 'unknown', and
    `should_act` refuses on unknown."""
    _transcript_folder(tmp_path, monkeypatch)
    hook.mark_started(str(project_dir), str(tmp_path / "gone.jsonl"))
    run_state._live_percent_cache.clear()

    percent = run_state.current_percent(str(project_dir))

    assert percent is None
    assert watch.should_act(percent, threshold=30, quiet_for=600) is False


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
    monkeypatch.setattr(watch, "current_percent", lambda _p: 99)
    monkeypatch.setattr(watch, "transcript_path", lambda *_a: "chat.jsonl")
    monkeypatch.setattr(watch, "idle_seconds", lambda *_a, **_k: quiet)
    # Обе тишины разведены: эти тесты про последний взгляд на экран, а не про
    # то, кто написал в транскрипт. Без мока файла нет — «не знаю» отменило бы
    # уборку раньше, чем очередь дойдёт до сравнения экранов.
    monkeypatch.setattr(watch, "human_idle_seconds", lambda *_a, **_k: quiet)
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


# --- handing the work back --------------------------------------------------


def test_the_continuation_is_typed_once_and_never_repeated(project_dir, keyboard, monkeypatch):
    """AC negative: this step starts work that runs for as long as the work
    takes. A retry would land a duplicate command on an agent mid-edit — the
    retry that protects the other steps is the worst thing that could happen
    to this one."""
    cycle_state.start_run(str(project_dir), "прогон объявлен")
    monkeypatch.setattr(watch, "confirm", lambda *_a, **_k: False)

    watch.deliver(
        str(project_dir), 111, "Продолжай прогон. Направление: очередь", watch.WAIT_SPEAKING
    )

    assert keyboard.typed.count("Продолжай прогон. Направление: очередь") == 1


def test_the_continuation_does_not_press_escape_first(project_dir, keyboard):
    """After `/start` the input line is empty, so Esc has nothing to clear —
    what it does instead is interrupt a turn that is still drawing itself. The
    run of 17:24 shows the cost: `Interrupted` where the fresh session's answer
    should have been."""
    cycle_state.start_run(str(project_dir), "прогон объявлен")
    watch.deliver(str(project_dir), 111, "Продолжай прогон", watch.WAIT_SPEAKING)

    assert keyboard.typed == ["Продолжай прогон"]


def test_the_other_steps_keep_their_retries(project_dir, keyboard, monkeypatch):
    """Right after `/clear` the chat reads nothing; one retry is the difference
    between a finished cycle and a silent stall."""
    cycle_state.start_run(str(project_dir), "прогон объявлен")
    monkeypatch.setattr(watch, "confirm", lambda *_a, **_k: False)

    watch.deliver(str(project_dir), 111, "/checkpoint", cycle_state.WAIT_TURN)

    assert keyboard.typed.count("/checkpoint") == watch.DELIVERY_ATTEMPTS


def test_the_cycle_hands_the_work_back_when_a_run_is_declared(project_dir, keyboard):
    cycle_state.start_run(str(project_dir), "разгреби очередь")

    watch.run_sequence(str(project_dir), 111, watch.Maintenance())

    assert keyboard.typed[-1] == "Продолжай прогон. Направление: разгреби очередь"


def test_a_delivery_is_measured_against_the_transcript_before_typing(
    project_dir, keyboard, monkeypatch
):
    """ "It answered" means it grew past what it held when the command went in —
    measured before the keys, or the growth being waited on is already there."""
    cycle_state.start_run(str(project_dir), "прогон объявлен")
    seen = []
    monkeypatch.setattr(watch, "transcript_size", lambda *_a, **_k: seen.append("measured") or 7)
    monkeypatch.setattr(
        watch, "confirm", lambda *_a, baseline=0, **_k: seen.append(baseline) is None
    )

    watch.deliver(str(project_dir), 111, "Продолжай", watch.WAIT_SPEAKING)

    assert seen == ["measured", 7]


def test_the_baseline_is_taken_after_the_draft_is_cleared(project_dir, keyboard, monkeypatch):
    """Esc interrupts a turn that is still drawing, and the interruption is
    written into the transcript. A baseline taken before it counts that growth
    as the chat answering: the run of 17:24 reported the continuation delivered
    in the same second it was typed, having confirmed itself against its own
    Esc."""
    cycle_state.start_run(str(project_dir), "прогон объявлен")
    events = []
    monkeypatch.setattr(
        watch.keys,
        "send_to_console",
        lambda _pid, text, submit=True: (events.append(text), (True, ""))[1],
    )
    monkeypatch.setattr(watch, "transcript_size", lambda *_a, **_k: events.append("measured") or 7)

    watch.deliver(str(project_dir), 111, "/checkpoint", cycle_state.WAIT_TURN)

    assert events == [watch.ESC, "measured", "/checkpoint"]


# --- withdrawing the run interrupts a cleanup already under way ------------


def withdraw_run(project_dir):
    """Exactly what `/auto стоп` does."""
    cycle_state.end_run(str(project_dir))


class TestStopInterruptsTheCycle:
    """Withdrawing the run used to change nothing until the cycle ended: the
    watcher waits minutes for a mark and retries up to three times. Seen live —
    the run was withdrawn at 23:00, /checkpoint was retyped at 23:03 into a
    chat the human had already released it from, and the process had to be
    killed by hand."""

    def test_the_next_command_is_not_typed_after_a_withdrawal(
        self, project_dir, keyboard, monkeypatch
    ):
        cycle_state.start_run(str(project_dir), "прогон объявлен")
        real = watch.deliver

        def withdraw_after_first(pd, pid, command, trace):
            result = real(pd, pid, command, trace)
            withdraw_run(project_dir)
            return result

        monkeypatch.setattr(watch, "deliver", withdraw_after_first)
        ok = watch.run_sequence(str(project_dir), 111, watch.Maintenance())

        assert ok is False
        assert keyboard.typed.count("/clear") == 0, "typed on after the run was withdrawn"

    def test_a_retry_does_not_survive_a_withdrawal(self, project_dir, keyboard, monkeypatch):
        """The retry loop is where the watcher used to spend those minutes."""
        cycle_state.start_run(str(project_dir), "прогон объявлен")

        def fail_then_withdraw(*_a, **_k):
            withdraw_run(project_dir)
            return False

        monkeypatch.setattr(watch, "confirm", fail_then_withdraw)
        sent, reason = watch.deliver(str(project_dir), 111, "/checkpoint", cycle_state.WAIT_TURN)

        assert sent is False
        assert keyboard.typed.count("/checkpoint") == 1, "retried after the withdrawal"
        assert "прогон снят" in reason

    def test_the_stop_file_interrupts_too(self, project_dir, keyboard, monkeypatch):
        cycle_state.start_run(str(project_dir), "прогон объявлен")

        def fail_then_stop(*_a, **_k):
            (project_dir / ".tausik" / ".chat-watch.stop").write_text("", encoding="utf-8")
            return False

        monkeypatch.setattr(watch, "confirm", fail_then_stop)
        sent, reason = watch.deliver(str(project_dir), 111, "/checkpoint", cycle_state.WAIT_TURN)

        assert sent is False
        assert keyboard.typed.count("/checkpoint") == 1
        assert "стоп" in reason

    def test_the_reason_reaches_the_log(self, project_dir, keyboard, monkeypatch):
        """A watcher that stops silently is indistinguishable from one that hung."""
        cycle_state.start_run(str(project_dir), "прогон объявлен")

        def fail_then_withdraw(*_a, **_k):
            withdraw_run(project_dir)
            return False

        monkeypatch.setattr(watch, "confirm", fail_then_withdraw)
        watch.deliver(str(project_dir), 111, "/checkpoint", cycle_state.WAIT_TURN)

        log = (project_dir / ".tausik" / "chat-watch.log").read_text(encoding="utf-8")
        assert "прогон снят" in log
        assert "подача прервана" in log

    def test_a_lock_left_by_a_dead_process_does_not_block(self, project_dir):
        """NEGATIVE: a killed watcher must not lock the project out. The pid in
        the lock is checked, so a stale one is simply taken over."""
        lock = project_dir / ".tausik" / ".chat-watch.lock"
        lock.write_text("999999999", encoding="utf-8")

        assert watch.take_lock(str(project_dir)) is True
        assert lock.read_text(encoding="utf-8").strip() != "999999999"
