"""When the context window gets cleaned, in what order, and what proves it.

The wrapper these rules once lived under is gone (dead end #17); the rules are
not. They decide when a chat may be wiped, and every one of them exists
because breaking it loses somebody's work.
"""

import autoloop_chat_cycle as cycle


# --- the maintenance cycle: order and refusals ----------------------------


def run_cycle(ready_answers):
    """Run the sequence with a scripted `ready`; returns what got typed."""
    typed, said = [], []
    answers = list(ready_answers)
    m = cycle.Maintenance(threshold=30)
    ok = m.run(
        send=typed.append,
        ready=lambda _t: answers.pop(0) if answers else False,
        announce=said.append,
    )
    return ok, typed, said


def test_state_reaches_disk_before_the_context_is_wiped():
    ok, typed, _ = run_cycle([True, True, True])

    assert ok is True
    assert typed == ["/checkpoint", "/clear", "/start"]


def test_clear_is_not_sent_when_the_chat_never_frees_up():
    """A finished checkpoint is the only other copy of the conversation. If the
    line does not free up, the wipe must not happen — a full window is a much
    smaller problem than a lost handoff."""
    ok, typed, said = run_cycle([True, False])

    assert ok is False
    assert typed == ["/checkpoint"]
    assert "/clear" not in typed
    assert said and "отменена" in said[0]


def test_nothing_is_typed_when_the_line_is_busy_from_the_start():
    ok, typed, _ = run_cycle([False])

    assert ok is False
    assert typed == []


# --- what the cycle consists of -------------------------------------------


def test_the_ordinary_cycle_does_not_touch_the_session():
    assert [c for c, _ in cycle.sequence()] == ["/checkpoint", "/clear", "/start"]


def test_a_spent_session_is_closed_between_the_handoff_and_the_wipe():
    assert [c for c, _ in cycle.sequence(close_session=True)] == [
        "/checkpoint",
        "/end",
        "/clear",
        "/start",
    ]


# --- what proves a command actually ran ------------------------------------


def test_a_turn_that_ended_confirms_the_command(project_dir):
    (project_dir / ".tausik" / ".chat.ready").write_text("1", encoding="utf-8")

    assert cycle.confirm(str(project_dir), cycle.WAIT_TURN) is True
    # Consumed: a leftover mark answers for the next command too.
    assert not (project_dir / ".tausik" / ".chat.ready").exists()


def test_a_session_that_came_back_confirms_the_wipe(project_dir, monkeypatch):
    """`/clear` ends no turn — waiting for the Stop flag after it waits
    forever, and waiting on a timer instead lost a run (dead end #19)."""
    monkeypatch.setattr(cycle, "SETTLE_AFTER_SESSION", 0)
    (project_dir / ".tausik" / ".chat.started").write_text("1", encoding="utf-8")

    assert cycle.confirm(str(project_dir), cycle.WAIT_SESSION) is True
    assert not (project_dir / ".tausik" / ".chat.started").exists()


def test_a_wipe_without_a_new_session_is_not_confirmed(project_dir, monkeypatch):
    """AC negative: keys written into a console that stopped reading are lost
    in silence — the write still reports success."""
    monkeypatch.setattr(cycle, "SESSION_TIMEOUT", 0.05)

    assert cycle.confirm(str(project_dir), cycle.WAIT_SESSION) is False


def test_a_command_without_a_finished_turn_is_not_confirmed(project_dir, monkeypatch):
    monkeypatch.setattr(cycle, "READY_TIMEOUT", 0.05)

    assert cycle.confirm(str(project_dir), cycle.WAIT_TURN) is False


# --- arming, cancelling, and refusing to guess ----------------------------


def test_a_full_window_arms_once_not_every_tick():
    m = cycle.Maintenance(threshold=30)

    assert m.consider(31, now=0.0) is True
    assert m.consider(31, now=1.0) is False  # already armed — announce once


def test_esc_cancels_and_the_wipe_does_not_fire():
    m = cycle.Maintenance(threshold=30, arm_seconds=15)
    m.consider(40, now=0.0)

    assert m.cancel() is True
    assert m.due(now=100.0) is False


def test_cancelling_when_nothing_is_pending_is_not_an_event():
    """Esc in an idle chat belongs to the chat, not to us."""
    assert cycle.Maintenance().cancel() is False


def test_the_wipe_waits_out_the_grace_period():
    m = cycle.Maintenance(threshold=30, arm_seconds=15)
    m.consider(40, now=0.0)

    assert m.due(now=14.9) is False
    assert m.due(now=15.0) is True


def test_the_same_reading_does_not_arm_twice():
    """Live defect: the reading stayed at 33%, so every tick re-armed and the
    chat got three warnings in a row."""
    m = cycle.Maintenance(threshold=30)
    m.consider(33, now=0.0)
    m.run(send=lambda _c: None, ready=lambda _t: True, announce=lambda _m: None)

    assert m.consider(33, now=100.0) is False


def test_a_window_that_actually_emptied_arms_again():
    m = cycle.Maintenance(threshold=30)
    m.consider(33, now=0.0)
    m.run(send=lambda _c: None, ready=lambda _t: True, announce=lambda _m: None)

    m.consider(4, now=110.0)  # the clear worked; the reading dropped

    assert m.consider(35, now=200.0) is True


def test_an_aborted_cleanup_also_stops_re_offering():
    m = cycle.Maintenance(threshold=30)
    m.consider(33, now=0.0)
    m.run(send=lambda _c: None, ready=lambda _t: False, announce=lambda _m: None)

    assert m.consider(33, now=50.0) is False


def test_a_refusal_holds_for_a_while():
    """Asking again ten seconds after Esc is the same as not asking."""
    m = cycle.Maintenance(threshold=30)
    m.consider(40, now=0.0)
    m.cancel(now=0.0, quiet_for=600)

    assert m.consider(40, now=300.0) is False
    assert m.consider(40, now=601.0) is True


def test_a_missing_measurement_is_not_a_full_window():
    """AC negative: no reading is not a reading of zero — and not of 100."""
    m = cycle.Maintenance(threshold=30)

    assert cycle.needs_maintenance(None, 30) is False
    assert m.consider(None, now=0.0) is False
    assert m.due(now=1000.0) is False


def test_a_boolean_is_not_a_percentage():
    """`True` is an int in Python; a truthy flag must not read as 100%."""
    assert cycle.needs_maintenance(True, 0) is False


def test_a_window_below_the_threshold_is_left_alone():
    assert cycle.needs_maintenance(29.9, 30) is False
    assert cycle.needs_maintenance(30, 30) is True


# --- one driver per project -----------------------------------------------


def test_a_live_lock_blocks_a_second_driver():
    """Two wrappers would fight over one readiness flag and type commands into
    each other's chat."""
    assert cycle.lock_is_stale(1234, running_pids=[1234]) is False


def test_a_lock_from_a_dead_driver_does_not_block():
    assert cycle.lock_is_stale(1234, running_pids=[]) is True
    assert cycle.lock_is_stale(None, running_pids=[999]) is True


# --- the draft nobody has sent yet ----------------------------------------


def test_a_screen_that_moved_means_somebody_is_typing():
    """The transcript's mtime moves only when a turn is sent, so a person a
    minute into a long message reads as a person who left."""
    assert cycle.draft_changed("> прив", "> привет, мир") is True


def test_a_still_screen_is_not_an_objection():
    assert cycle.draft_changed("> ", "> ") is False


def test_a_screen_nobody_could_read_is_not_an_objection():
    """AC negative: a failed screen read must not freeze the mechanism for
    good — the mtime check still stands behind it."""
    assert cycle.draft_changed(None, "> черновик") is False
    assert cycle.draft_changed("> черновик", None) is False
    assert cycle.draft_changed(None, None) is False


# --- the step that hands the work back --------------------------------------


def test_the_cycle_ends_by_returning_the_chat_to_work():
    """Without it the cycle stops on /start: context clean, session open, and
    the chat waiting for a human who said they were leaving."""
    steps = cycle.sequence(direction="очередь задач")

    assert [command for command, _ in steps][:3] == ["/checkpoint", "/clear", "/start"]
    assert steps[-1][1] == cycle.WAIT_SPEAKING
    assert "очередь задач" in steps[-1][0]


def test_the_direction_travels_in_the_text():
    """After the wipe nothing else remembers what the work was about."""
    steps = cycle.sequence(direction="почини вёрстку на главной")

    assert "почини вёрстку на главной" in steps[-1][0]


def test_without_a_direction_the_chat_is_not_told_anything():
    """AC negative: no direction is no run — a command into the void would put
    a cleaned chat to work on nothing in particular."""
    steps = cycle.sequence()

    assert [command for command, _ in steps] == ["/checkpoint", "/clear", "/start"]


def test_a_spent_session_still_ends_on_the_continuation():
    """/end belongs before the wipe; the continuation after the new session."""
    steps = cycle.sequence(close_session=True, direction="очередь задач")

    assert [command for command, _ in steps][:4] == [
        "/checkpoint",
        "/end",
        "/clear",
        "/start",
    ]
    assert steps[-1][1] == cycle.WAIT_SPEAKING


def test_a_chat_that_started_answering_counts_as_delivered(project_dir, monkeypatch):
    """The work runs for as long as it takes; "it started" is the only answer
    this step can be given."""
    sizes = iter([10, 10, 4096])
    monkeypatch.setattr("autoloop_presence.transcript_size", lambda *_a, **_k: next(sizes))

    assert cycle.wait_speaking(str(project_dir), baseline=10, sleep=lambda _s: None) is True


def test_a_chat_that_never_answers_is_not_delivered(project_dir, monkeypatch):
    """AC negative: a command typed into a console that stopped reading must
    not be reported as done."""
    monkeypatch.setattr("autoloop_presence.transcript_size", lambda *_a, **_k: 10)
    monkeypatch.setattr(cycle.time, "monotonic", iter([0.0, 1.0, 999.0]).__next__)

    assert cycle.wait_speaking(str(project_dir), baseline=10, sleep=lambda _s: None) is False


# --- the input box, told apart from the rest of a living screen -------------
#
# The rows below are a transcript of two real chats, read through
# `autoloop_keys.console_text` while nobody touched the keyboard. Both had the
# same shape, and two of the twelve rows changed within six seconds by
# themselves — which is the whole defect.

RULE = "─" * 118


def screen(draft: str = "", *, spinner: str = "4m 35s", clock: str = "2h 13m") -> str:
    """The measured layout: conversation, spinner, box, status bar."""
    return "\n".join(
        [
            "     git log --oneline -5",
            '     echo "=== дерево:"; git status --porcelain | wc -l',
            "",
            f"✢ Zigzagging… ({spinner} · ↓ 13.1k tokens)",
            "",
            RULE,
            f"❯ {draft}".rstrip(),
            RULE,
            "  [Opus 5 (1M context)] │ git:(feat/mod-ports-pipeline) │ ⏱️  2h 14m",
            f"  Context █████░░░░░ 53% │ Usage ██░░░░░░░░ 21% (resets in {clock})",
            "  2 CLAUDE.md | 1 rules | 8 MCPs | 9 hooks",
            "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← 1 agent",
        ]
    )


def test_a_ticking_screen_is_not_somebody_typing():
    """The one that cost 23 cleanups in a day. The spinner counts seconds and
    the status bar counts minutes; against the whole screen tail both read as a
    person at the keyboard, and each false reading bought ten minutes of
    refusal cooldown while the window climbed from 30% to 50.2%."""
    before = screen(spinner="4m 2s", clock="2h 14m")
    after = screen(spinner="4m 8s", clock="2h 13m")

    assert before != after  # the screens really do differ
    assert cycle.draft_changed(before, after) is False


def test_a_draft_in_the_box_still_stops_the_cleanup():
    """AC negative: the guard exists for exactly this, and narrowing the
    comparison must not cost it. Typed while the clocks also moved."""
    before = screen("прив", spinner="4m 2s")
    after = screen("привет, мир", spinner="4m 8s")

    assert cycle.draft_changed(before, after) is True


def test_a_draft_that_grew_a_line_stops_the_cleanup():
    """A multi-line draft makes the box taller; the rows between the rules
    change in number, which is the same answer."""
    before = screen("первая строка")
    after = before.replace("❯ первая строка", "❯ первая строка\n  вторая строка")

    assert cycle.draft_changed(before, after) is True


def test_a_screen_without_a_box_is_judged_whole():
    """AC negative: an unparsed screen must not answer «no box, so no draft».
    Another TUI, or a window too short to show the input, falls back to the
    comparison this replaced rather than to permission to wipe."""
    assert cycle.input_box("> прив") is None
    assert cycle.draft_changed("> прив", "> привет") is True


def test_two_rules_with_nothing_between_them_are_not_a_box():
    """A box has to hold something. Adjacent rules are a divider."""
    assert cycle.input_box("\n".join(["текст", RULE, RULE, "  статусбар"])) is None


def test_the_box_is_the_lowest_pair_of_rules():
    """A rule drawn in the conversation above must not widen the box to
    include the spinner."""
    drawn_in_the_conversation = "\n".join(["итог:", RULE, "текст"]) + "\n" + screen("черновик")

    assert cycle.input_box(drawn_in_the_conversation) == ["❯ черновик"]


# --- winding the run down before the window is wiped -------------------------


class TestTheRunIsAskedToFinishFirst:
    """Раньше уборка приходила в любую 45-секундную паузу — то есть могла лечь
    посреди задачи, а сам прогон о заполнении окна не знал и брал ещё работу."""

    def cycle(self, **kw):
        return cycle.Maintenance(threshold=50, **kw)

    def armed(self, **kw):
        machine = self.cycle(**kw)
        machine.consider(99, now=0.0)
        return machine

    def test_the_request_says_what_to_do_and_fits_one_line(self):
        """Печатается в консоль, а перевод строки там — это отправка."""
        text, trace = cycle.wind_down_step("очередь задач")

        assert "\n" not in text
        assert "Новую задачу не начинай" in text
        assert "очередь задач" in text
        assert trace == cycle.WAIT_SPEAKING

    def test_a_run_without_a_direction_is_still_asked_to_stop(self):
        text, _ = cycle.wind_down_step("")

        assert "Направление" not in text and "остановись" in text

    def test_the_countdown_leads_to_winding_not_to_the_wipe(self):
        machine = self.armed()
        assert machine.due(now=99.0) is True

        machine.wind(now=99.0, wall=1000.0)

        assert machine.state == cycle.STATE_WINDING
        assert machine.due(now=200.0) is False  # отсчёт кончился, он не повторяется

    def test_the_wipe_waits_until_the_run_actually_stood(self):
        """AC-2: тишина берётся у флага Stop-хука, а не у транскрипта."""
        machine = self.armed()
        machine.wind(now=99.0, wall=1000.0)

        assert machine.wound_up(standing_for=None, now=120.0) is False
        assert machine.wound_up(standing_for=10.0, now=120.0) is False
        assert machine.wound_up(standing_for=600.0, now=120.0) is True

    def test_work_in_flight_holds_the_wipe(self):
        machine = self.armed()
        machine.wind(now=99.0, wall=1000.0)

        assert machine.wound_up(standing_for=600.0, now=120.0, busy=True) is False

    def test_a_task_that_never_winds_down_does_not_hold_the_window_for_ever(self):
        """AC-3 НЕГАТИВНЫЙ: право вето вернуло бы исходный дефект — окно,
        которое не чистится никогда."""
        machine = self.armed(wind_timeout=1800.0)
        machine.wind(now=100.0, wall=1000.0)

        assert machine.wound_up(standing_for=None, now=100.0 + 1799.0) is False
        assert machine.wound_up(standing_for=None, now=100.0 + 1801.0) is True
        assert machine.timed_out(now=100.0 + 1801.0) is True

    def test_a_human_who_came_back_cancels_the_winding_too(self):
        """AC-4 НЕГАТИВНЫЙ: просьба свернуться уже подана и вреда не несёт, а
        вытирать чат из-под вернувшегося человека нельзя."""
        machine = self.armed()
        machine.wind(now=99.0, wall=1000.0)

        assert machine.cancel(now=120.0) is True
        assert machine.state == cycle.STATE_IDLE
        assert machine.cooling_for(now=120.0) == cycle.CANCEL_QUIET

    def test_our_own_request_is_not_a_human_coming_back(self):
        """Наблюдатель печатает просьбу В ЧАТ, поэтому она ложится в транскрипт
        репликой человека — и отменила бы уборку, которая её и послала."""
        machine = self.armed()
        machine.wind(now=99.0, wall=1000.0)

        assert machine.echo_of_us(human_at=1000.5) is True
        assert machine.echo_of_us(human_at=1010.0) is False  # это уже человек

    def test_nothing_is_an_echo_before_the_request_was_sent(self):
        """НЕГАТИВНЫЙ: до свёртки поблажки нет — иначе обычный обратный отсчёт
        перестанет отменяться человеком."""
        machine = self.armed()

        assert machine.echo_of_us(human_at=1000.5) is False

    def test_the_cycle_returns_to_idle_by_itself(self):
        """AC-5: раньше из ARMED его выводила ложная отмена «человек вернулся»
        тиком позже — она видна в логе после каждой удачной уборки. Без этой
        случайности последовательность повторилась бы на следующем тике."""
        machine = self.armed()
        machine.wind(now=99.0, wall=1000.0)

        machine.finish()

        assert machine.state == cycle.STATE_IDLE
        assert machine.awaiting_drop is True
        assert machine.wound_up(standing_for=600.0, now=200.0) is False


class TestTheAnchorTellsTheRunToDecideForItself:
    """Якорь бьёт по чату, который стоит. Чаще всего он стоит на вопросе,
    который сам и задал, — а «продолжай» оставляет его свободным задать тот же
    вопрос снова. Отвечать некому: это и значит объявленный прогон."""

    def test_it_allows_the_run_to_answer_its_own_question(self):
        text, trace = cycle.anchor_step("очередь задач")

        assert "прими решение сам" in text
        assert "task log" in text
        assert "очередь задач" in text
        assert trace == cycle.WAIT_SPEAKING

    def test_it_names_the_way_out_of_something_impassable(self):
        """Иначе «реши сам» толкает выдумывать обходной путь молча."""
        text, _ = cycle.anchor_step("")

        assert "task block" in text and "Направление" not in text

    def test_it_fits_one_line(self):
        """Печатается в консоль, где перевод строки — это отправка."""
        assert "\n" not in cycle.anchor_step("очередь")[0]

    def test_it_is_not_the_step_that_follows_a_wipe(self):
        """AC-2: после /clear вопроса не было — контекст стёрт, и разрешать
        нечего. Два состояния, два текста."""
        after_wipe, _ = cycle.continue_step("очередь")
        standing, _ = cycle.anchor_step("очередь")

        assert after_wipe != standing
        assert "прими решение сам" not in after_wipe
