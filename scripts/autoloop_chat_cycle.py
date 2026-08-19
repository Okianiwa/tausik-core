"""When to clean the window, and the order it has to happen in.

Kept apart from the terminal plumbing on purpose: this is the part that can be
wrong in a way tests can catch. Every decision here is a rule about timing,
and all three rules exist because breaking them loses work:

  * never type without the readiness flag — an Enter sent during a turn can
    answer an open permission dialog;
  * never send `/clear` before `/checkpoint` has finished — the handoff has
    not reached the database yet, and the context is the only other copy;
  * never clean on a missing measurement — no reading is not a reading of
    zero.
"""

from __future__ import annotations

import json
import os
import time

READY_FLAG = os.path.join(".tausik", ".chat.ready")
STARTED_FLAG = os.path.join(".tausik", ".chat.started")
LOCK_FILE = os.path.join(".tausik", ".chat.lock")
RUN_FILE = os.path.join(".tausik", ".chat-loop.json")


def run_path(project_dir: str) -> str:
    return os.path.join(project_dir, RUN_FILE)


def start_run(project_dir: str, direction: str, now: str = "") -> bool:
    """Declare a run: this chat may be watched, and this is what it works on.

    A file rather than a config flag, because a flag is permanent and this is
    not: a person who opens a chat to do their own work has not asked for a
    watcher, and `autoloop.watch: true` gave them one anyway, in every session,
    forever. The file exists only between the command that starts a run and the
    one that stops it.

    It also carries the direction across `/clear` — after a wipe nothing else
    remembers what the work was about.
    """
    direction = (direction or "").strip()
    if not direction:
        return False
    try:
        with open(run_path(project_dir), "w", encoding="utf-8") as f:
            json.dump({"direction": direction, "started_at": now}, f, ensure_ascii=False)
    except OSError:
        return False
    return True


def read_run(project_dir: str):
    """The declared run, or None. Anything unreadable is "no run".

    Never a crash and never a default: a half-written file is not permission to
    start typing into somebody's chat.
    """
    try:
        with open(run_path(project_dir), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    direction = str(data.get("direction") or "").strip()
    return {**data, "direction": direction} if direction else None


def run_direction(project_dir: str) -> str:
    run = read_run(project_dir)
    return run["direction"] if run else ""


def run_declared(project_dir: str) -> bool:
    return read_run(project_dir) is not None


def end_run(project_dir: str) -> None:
    try:
        os.unlink(run_path(project_dir))
    except OSError:
        pass


# Order matters: state to disk, then wipe, then reload it.
#
# Every command names the trace that proves it arrived. `/clear` ends no turn,
# so the Stop flag never comes for it — but it restarts the session, and the
# SessionStart hook leaves its own mark. Waiting on a timer instead cost a run:
# four seconds after `/clear` the chat was still rebuilding itself, the keys
# for `/start` fell on the floor, and the Enter behind them re-sent the leftover
# `/clear` (dead end #19).
WAIT_TURN = "turn"  # Stop hook — a model turn finished
WAIT_SESSION = "session"  # SessionStart hook — the chat came back up
# The fourth step starts work that runs for as long as the work takes. Waiting
# for the turn to end would call it undelivered after four minutes and type it
# again — Esc first — straight over an agent mid-edit. "It started" is the only
# answer this step can be given, and the transcript growing is what says so.
WAIT_SPEAKING = "speaking"

SEQUENCE = (
    ("/checkpoint", WAIT_TURN),
    ("/clear", WAIT_SESSION),
    ("/start", WAIT_TURN),
)
# Closing the session belongs to the clock, not to the window. A full context
# says nothing about how long the work has been running, and a session closed
# early throws away the history the metrics are counted from.
END_STEP = ("/end", WAIT_TURN)


def continue_step(direction: str):
    """The step that hands the chat back its work.

    Without it the cycle ends on `/start`: context clean, session open, and the
    chat sitting there waiting for a human who said they were leaving. The
    direction travels in the text itself — after the wipe there is nothing left
    that remembers what the work was about.
    """
    return (f"Продолжай прогон. Направление: {direction}", WAIT_SPEAKING)


def anchor_step(direction: str = ""):
    """The step that un-sticks a run standing still, as opposed to a cleaned one.

    Separate from `continue_step` because the two states differ in what the
    chat is stuck ON. After `/clear` there is nothing pending — the context is
    gone and the work simply has to restart. A run that has been standing for
    ten minutes, though, is usually standing on a question it asked, and being
    told «carry on» leaves it free to ask the same question again. Nobody is
    there to answer either one: that is what a declared run means.

    One line, no newlines: typed into a console, where a newline submits.
    """
    tail = f" Направление прогона: {direction}" if direction else ""
    return (
        "Продолжай прогон сам. Если ты остановился на вопросе — человека рядом нет: "
        "прими решение сам, запиши его и причину через `task log`, и работай дальше. "
        "Если упёрся во что-то непроходимое — заблокируй задачу с причиной "
        "(`task block`) и возьми следующую." + tail,
        WAIT_SPEAKING,
    )


def sequence(close_session: bool = False, direction: str = ""):
    """The commands to type, in order.

    `/end` goes after the checkpoint and before the wipe: the handoff has to be
    written while the conversation it summarises still exists. The continuation
    goes last, once the fresh session has finished reading itself in.
    """
    steps = SEQUENCE if not close_session else SEQUENCE[:1] + (END_STEP,) + SEQUENCE[1:]
    return steps + (continue_step(direction),) if direction else steps


ARM_SECONDS = 15.0
READY_TIMEOUT = 240.0
SESSION_TIMEOUT = 90.0
SETTLE_AFTER_SESSION = 3.0  # the flag means "started", not "finished drawing"
SPEAKING_TIMEOUT = (
    90.0  # long enough for a fresh session to think, short enough to notice a dead command
)
KEY_SETTLE = 0.3  # between Esc and the command, so they are not read as one
DELIVERY_ATTEMPTS = 3
CANCEL_QUIET = 600.0  # how long a refusal holds before offering again

STATE_IDLE = "idle"
STATE_ARMED = "armed"
STATE_DONE = "done"


def flag_path(project_dir: str) -> str:
    return os.path.join(project_dir, READY_FLAG)


def is_ready(project_dir: str) -> bool:
    return os.path.exists(flag_path(project_dir))


def turn_ended_at(project_dir: str) -> float | None:
    """Wall clock of the last turn END, as the HOST reported it, or None.

    The Stop hook writes this flag (`hooks/chat_ready.py`), so its mtime is the
    only thing in the system that separates «the turn ended» from «the turn is
    running quietly». The transcript's own clock cannot: a long tool call writes
    nothing for minutes and reads exactly like an empty room. Measured — a
    delivery landed on an agent 20 minutes into `gameprobe-run.sh`, and the host
    queued it because the turn was still running.

    Read, never consumed. `_wait_flag` eats the flag on purpose, because a
    leftover answers the next question before it is asked; a caller asking «how
    long has it been standing» must leave it for the next tick.
    """
    try:
        return os.path.getmtime(flag_path(project_dir))
    except OSError:
        return None


def clear_ready(project_dir: str) -> None:
    try:
        os.unlink(flag_path(project_dir))
    except OSError:
        pass


def wait_ready(project_dir: str, timeout: float, sleep=time.sleep) -> bool:
    """Block until the host says the input line is free."""
    return _wait_flag(flag_path(project_dir), timeout, sleep)


def started_path(project_dir: str) -> str:
    return os.path.join(project_dir, STARTED_FLAG)


def clear_started(project_dir: str) -> None:
    try:
        os.unlink(started_path(project_dir))
    except OSError:
        pass


def wait_started(project_dir: str, timeout: float, sleep=time.sleep) -> bool:
    """Block until a new session has come up — the only proof `/clear` landed."""
    return _wait_flag(started_path(project_dir), timeout, sleep)


def _wait_flag(path: str, timeout: float, sleep=time.sleep) -> bool:
    """Consume the flag on arrival: a leftover one answers the next question
    before it is asked."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass
            return True
        sleep(0.1)
    return False


def wait_speaking(
    project_dir: str, baseline: int, timeout=SPEAKING_TIMEOUT, sleep=time.sleep
) -> bool:
    """Block until the chat starts answering — its transcript outgrows what it
    was when the command went in.

    Deliberately not "the turn ended": the work this step starts is the point,
    and it takes as long as it takes.
    """
    from autoloop_presence import transcript_size

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if transcript_size(project_dir) > baseline:
            return True
        sleep(0.5)
    return False


def confirm(project_dir: str, trace: str, sleep=time.sleep, baseline: int = 0) -> bool:
    """Did the command actually run? Nothing else here knows.

    Writing into a console buffer succeeds whether or not the chat is reading
    it, so `sent` is not `arrived` — the run that failed logged three successes
    for two commands.
    """
    if trace == WAIT_TURN:
        return wait_ready(project_dir, READY_TIMEOUT, sleep)
    if trace == WAIT_SESSION:
        if not wait_started(project_dir, SESSION_TIMEOUT, sleep):
            return False
        sleep(SETTLE_AFTER_SESSION)
        return True
    if trace == WAIT_SPEAKING:
        return wait_speaking(project_dir, baseline, sleep=sleep)
    return True


def needs_maintenance(percent, threshold) -> bool:
    """A missing measurement is not a full window."""
    if not isinstance(percent, (int, float)) or isinstance(percent, bool):
        return False
    return bool(percent >= threshold)


# The input box is drawn with box-drawing rules. ASCII dashes are deliberately
# absent: a line of them in the conversation would be taken for the box, and a
# wrong box is worse than none — the fallback compares everything and is safe.
BOX_CHARS = frozenset("─━═╌╍┄┅┈┉")
MIN_BORDER = 10  # shorter runs occur inside text; the box spans the window


def _is_border(row: str) -> bool:
    """A rule drawn by the TUI, as opposed to a line of text that resembles one."""
    text = row.strip()
    return len(text) >= MIN_BORDER and set(text) <= BOX_CHARS


def input_box(screen: str) -> list[str] | None:
    """The rows that can hold a draft, or None when the box is not on screen.

    The layout, measured identically on two live chats: transcript tail and
    spinner, a rule, the prompt row, a rule, then the status bar. So the draft
    is what lies strictly between the two LOWEST rules — everything above is
    the conversation, everything below is a status bar with a clock in it.

    None rather than an empty list when the rules are not found: a screen this
    cannot parse (another TUI, a window too short to show the box) must fall
    back to comparing everything, never to «no box, so no draft».
    """
    rows = screen.split("\n")
    rules = [i for i, row in enumerate(rows) if _is_border(row)]
    if len(rules) < 2:
        return None
    top, bottom = rules[-2], rules[-1]
    if bottom - top < 2:
        return None  # two rules with nothing between them are not a box
    return rows[top + 1 : bottom]


def draft_changed(before: str | None, after: str | None) -> bool:
    """Did the DRAFT move while the countdown ran?

    Only a comparison, never a reading of the input line itself: a chat that
    shows a hint where the draft would be looks "occupied" forever, and a
    watcher that believes it never cleans anything again.

    Compared over the input box alone, because the rest of the screen moves on
    its own. Measured on two live chats: within six seconds and without a key
    pressed, the spinner row («Zigzagging… (4m 2s · ↓ 12.0k tokens)») and the
    status bar's own clock («resets in 2h 14m») both changed. Against the whole
    screen tail that reads as somebody typing — 23 of 31 cleanups in one day
    were cancelled that way, each costing ten minutes of refusal cooldown,
    while the window climbed from 30% to 50.2% uncleaned.

    A snapshot that is missing on either side answers False: not looking is
    not evidence of stillness, and the mtime check still stands behind this.
    """
    if not (before and after):
        return False
    box_before = input_box(before)
    box_after = input_box(after)
    if box_before is None or box_after is None:
        return before != after  # no box found: judge on everything, as before
    return box_before != box_after


def read_lock(project_dir: str):
    try:
        with open(os.path.join(project_dir, LOCK_FILE), encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def lock_is_stale(pid, running_pids) -> bool:
    """A lock from a driver that died is not a reason to refuse."""
    return pid is None or str(pid) not in {str(p) for p in running_pids}


def take_lock(project_dir: str, pid: int) -> None:
    path = os.path.join(project_dir, LOCK_FILE)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(pid))
    except OSError:
        pass


def release_lock(project_dir: str) -> None:
    try:
        os.unlink(os.path.join(project_dir, LOCK_FILE))
    except OSError:
        pass


class Maintenance:
    """Arms on a full window, waits out a grace period, then runs the sequence.

    The grace period is the whole difference between a wrapper and a tool that
    wipes the conversation from under the person typing into it.
    """

    def __init__(
        self,
        threshold=30,
        arm_seconds=ARM_SECONDS,
        ready_timeout=READY_TIMEOUT,
    ):
        self.threshold = threshold
        self.arm_seconds = arm_seconds
        self.ready_timeout = ready_timeout
        self.state = STATE_IDLE
        self.armed_at: float | None = None
        self.quiet_until = 0.0
        # After one cleanup the window must be seen empty before another is
        # offered. Without this a stuck reading re-arms on every tick and the
        # chat fills with warnings — observed live, three in a row.
        self.awaiting_drop = False

    def consider(self, percent, now: float) -> bool:
        """Start the countdown when the window is full. Returns True the moment
        it arms, so the caller announces it exactly once."""
        if not needs_maintenance(percent, self.threshold):
            self.awaiting_drop = False
            return False
        if self.state != STATE_IDLE or self.awaiting_drop or now < self.quiet_until:
            return False
        self.state = STATE_ARMED
        self.armed_at = now
        return True

    def cancel(self, now: float = 0.0, quiet_for: float = CANCEL_QUIET) -> bool:
        """The human said no. Returns True when there was something to cancel.

        A refusal holds: asking again ten seconds later is the same as not
        asking at all."""
        if self.state != STATE_ARMED:
            return False
        self.state = STATE_IDLE
        self.armed_at = None
        self.quiet_until = now + quiet_for
        return True

    def cooling_for(self, now: float) -> float:
        """Seconds left on the refusal, so the window can say so.

        The state cost a question out loud: after a cancel the plaque still
        read «жду тишины · 695 с», and the human read it as the watcher waiting
        for a pause that had already lasted eleven minutes. It was serving a
        refusal, and nothing on screen said which.
        """
        return max(0.0, self.quiet_until - now)

    def due(self, now: float) -> bool:
        return (
            self.state == STATE_ARMED
            and self.armed_at is not None
            and (now - self.armed_at) >= self.arm_seconds
        )

    def finish(self) -> None:
        """Back to idle after the sequence ran.

        Explicitly, because it used not to be. `run_sequence` left the cycle
        ARMED, and the only thing that moved it on was the human-returned
        branch firing a tick later against the run's OWN writing — visible in
        the log as «отменено: человек вернулся в чат» two seconds after every
        completed cleanup. Without that accident the sequence would have
        repeated on the very next tick.
        """
        self.state = STATE_IDLE
        self.armed_at = None
        self.awaiting_drop = True

    def run(self, send, ready, announce, confirm=None) -> bool:
        """The sequence itself.

        Two gates, not one. `ready(timeout)` before the first command, because
        typing into a running turn can answer a permission dialog. Then
        `confirm(trace)` after each one, because a command that was typed is
        not a command that ran — and the step nobody confirmed is exactly the
        step that silently failed. Aborting before `/clear` is the whole point:
        a half-finished checkpoint plus a wiped context is the one outcome
        worse than a full window.
        """
        self.state = STATE_DONE

        def stop(command: str) -> bool:
            announce(
                f"[chat] очистка отменена: чат не освободился перед {command}. "
                "Контекст не тронут; при необходимости запусти /checkpoint вручную."
            )
            self.state = STATE_IDLE
            self.awaiting_drop = True  # do not re-offer on the same reading
            return False

        if not ready(self.ready_timeout):
            return stop(SEQUENCE[0][0])
        for command, trace in SEQUENCE:
            send(command)
            landed = (
                confirm(trace) if confirm else (trace != WAIT_TURN or ready(self.ready_timeout))
            )
            if not landed:
                return stop(command)
        self.state = STATE_IDLE
        self.awaiting_drop = True
        return True
