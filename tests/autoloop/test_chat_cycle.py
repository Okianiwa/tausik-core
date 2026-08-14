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
