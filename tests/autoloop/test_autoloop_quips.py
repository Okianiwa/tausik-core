"""The cat's voice: what it says, when it changes, and when it stays quiet."""

import random

import autoloop_quips as quips
import autoloop_tui as tui
from autoloop_quips import QuipPicker, ellipsize, reason_for

HOLD = quips.HOLD_SECONDS


def snapshot(**over):
    """A collect()-shaped dict — only the keys the voice actually reads."""
    data = {
        "status": tui.STATUS_RUNNING,
        "current_task": "some-task",
        "percent": 12.0,
        "soft_threshold": 30,
        "tasks_done": [],
    }
    data.update(over)
    return data


def picker(seed=7, hold=HOLD):
    return QuipPicker(hold_seconds=hold, rng=random.Random(seed))


# --- reason follows the snapshot -----------------------------------------


def test_running_with_a_task_is_working():
    assert reason_for(snapshot()) == quips.REASON_WORKING


def test_running_without_a_task_is_a_start():
    """The run is live but no iteration has opened yet."""
    assert reason_for(snapshot(current_task=None)) == quips.REASON_START


def test_context_over_the_threshold_outranks_the_task():
    assert reason_for(snapshot(percent=31.0)) == quips.REASON_CONTEXT_HIGH


def test_unmeasured_context_is_not_a_full_window():
    assert reason_for(snapshot(percent=None)) == quips.REASON_WORKING


def test_failed_run_speaks_about_the_failure():
    assert reason_for(snapshot(status=tui.STATUS_FAILED)) == quips.REASON_FAILED


def test_stopped_and_idle_are_distinct():
    assert reason_for(snapshot(status=tui.STATUS_STOPPED)) == quips.REASON_STOPPED
    assert reason_for(snapshot(status=tui.STATUS_IDLE)) == quips.REASON_IDLE


def test_closing_a_task_outranks_everything():
    assert reason_for(snapshot(), task_just_closed=True) == quips.REASON_TASK_DONE


# --- what lands on screen -------------------------------------------------


def test_the_line_names_the_task_it_is_chewing():
    seen = {
        picker(seed).update(snapshot(current_task="autoloop-quips"), 0.0)
        for seed in range(20)
    }

    assert any("autoloop-quips" in line for line in seen)


def test_a_line_holds_instead_of_flickering_every_frame():
    """The animation ticks every 450ms; the voice must not."""
    p = picker()
    first = p.update(snapshot(), 0.0)

    assert p.update(snapshot(), 0.45) == first
    assert p.update(snapshot(), HOLD - 0.1) == first


def test_the_same_reason_never_repeats_the_same_line_twice():
    p = picker()
    first = p.update(snapshot(), 0.0)
    second = p.update(snapshot(), HOLD)

    assert second != first


def test_news_interrupts_the_current_line():
    """A changed reason speaks immediately — waiting out the hold would report
    a fallen iteration seven seconds late."""
    p = picker()
    working = p.update(snapshot(), 0.0)
    failed = p.update(snapshot(status=tui.STATUS_FAILED), 0.5)

    assert failed != working
    assert failed in quips.QUIPS[quips.REASON_FAILED]


def test_a_closed_task_is_announced_and_stays_up():
    p = picker()
    p.update(snapshot(tasks_done=["one"]), 0.0)
    announced = p.update(snapshot(tasks_done=["one", "two"]), 1.0)

    assert announced in quips.QUIPS[quips.REASON_TASK_DONE]
    # The count stops growing on the next tick — the news must not vanish with it.
    assert p.update(snapshot(tasks_done=["one", "two"]), 2.0) == announced


def test_the_announcement_eventually_gives_way():
    p = picker()
    p.update(snapshot(tasks_done=["one"]), 0.0)
    p.update(snapshot(tasks_done=["one", "two"]), 1.0)
    later = p.update(snapshot(tasks_done=["one", "two"]), 1.0 + HOLD + 0.1)

    assert later not in quips.QUIPS[quips.REASON_TASK_DONE]


# --- AC negative: quiet, never fatal --------------------------------------


def test_an_empty_snapshot_is_quiet_not_fatal():
    """No journal, no readings — the first seconds of a fresh project."""
    p = picker()

    assert isinstance(p.update({}, 0.0), str)
    assert isinstance(p.update(None, 1.0), str)
    assert reason_for(None) == quips.REASON_IDLE


def test_an_unknown_status_falls_back_to_a_neutral_line():
    p = picker()
    line = p.update(snapshot(status="teleported"), 0.0)

    assert line in quips.QUIPS[quips.REASON_IDLE]


def test_a_partial_snapshot_does_not_raise():
    """Keys can be missing entirely — collect() is not the only caller shape."""
    p = picker()

    assert isinstance(p.update({"status": tui.STATUS_RUNNING}, 0.0), str)
    assert isinstance(p.update({"tasks_done": None, "percent": "nope"}, 5.0), str)


# --- AC negative: no invented facts ---------------------------------------


def test_without_a_task_the_line_never_names_one():
    for seed in range(30):
        p = picker(seed)
        for tick in range(6):
            line = p.update(snapshot(current_task=None), tick * HOLD)
            assert "None" not in line
            assert "{task}" not in line
            assert "some-task" not in line


def test_a_blank_task_counts_as_no_task():
    assert reason_for(snapshot(current_task="   ")) == quips.REASON_START


# --- data, not code -------------------------------------------------------


def test_every_reason_has_lines_to_say():
    reasons = {
        quips.REASON_START,
        quips.REASON_WORKING,
        quips.REASON_TASK_DONE,
        quips.REASON_CONTEXT_HIGH,
        quips.REASON_FAILED,
        quips.REASON_STOPPED,
        quips.REASON_IDLE,
    }

    assert reasons <= set(quips.QUIPS)
    for reason, lines in quips.QUIPS.items():
        # Two at minimum, or "never the same line twice" has nothing to pick.
        assert len(lines) >= 2, reason
        assert all(line.strip() for line in lines), reason


def test_only_the_working_lines_carry_a_task_slot():
    for reason, lines in quips.QUIPS.items():
        if reason == quips.REASON_WORKING:
            continue
        assert not any("{task}" in line for line in lines), reason


def test_a_long_line_is_cut_to_fit_the_window():
    long_slug = "a" * 80
    line = QuipPicker(rng=random.Random(1)).update(
        snapshot(current_task=long_slug), 0.0
    )

    assert len(line) <= quips.QUIP_MAX_CHARS
    assert line.endswith("…")


def test_ellipsize_leaves_short_text_alone():
    assert ellipsize("коротко") == "коротко"


def test_the_voice_reads_nothing_from_disk_or_network():
    """AC: it turns a snapshot into a sentence. Anything else is somebody
    else's job — and a second reader of the same files is how the numbers
    drifted last time."""
    import inspect

    source = inspect.getsource(quips)

    for forbidden in ("open(", "glob", "sqlite3", "requests", "urllib", "subprocess"):
        assert forbidden not in source, forbidden
    # No clock either: the caller passes `now`, which is what makes the hold
    # testable without sleeping.
    assert "import time" not in source
    assert "monotonic" not in source
