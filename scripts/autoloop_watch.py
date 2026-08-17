"""Watches one chat's context window and clears it when it fills up.

Runs beside the chat, never between it and its terminal — that was the wrapper
approach and it failed on principle (dead end #17). Here the chat is an
ordinary `claude`, and commands reach it through its console's input buffer
(autoloop_keys). Nothing about its drawing or its keyboard is involved.

Three refusals hold the whole design together:
  * it acts only while the session is idle — a person mid-thought must not
    have their conversation wiped;
  * it types only after the Stop hook says a turn ended;
  * it never sends `/clear` before `/checkpoint` came back — the handoff in
    the database is the only other copy of what is being erased.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import IO

import autoloop_clean_request as clean_request
import autoloop_keys as keys
import autoloop_watch_state as wstate
from autoloop_limits import session_spent, status_text  # noqa: F401 — status_text: tests
from autoloop_run_state import (  # noqa: F401 — MAX_READING_AGE, reading: tests
    MAX_READING_AGE,
    current_percent,
    reading,
)
from autoloop_presence import (
    background_pids,
    busy_pids,
    human_idle_seconds,
    idle_seconds,
    own_pids,
    register_own,
    transcript_path,
    transcript_size,
    worker_name,
)
from autoloop.state import load_config
from autoloop_chat_cycle import (
    ARM_SECONDS,
    DELIVERY_ATTEMPTS,
    KEY_SETTLE,
    Maintenance,
    clear_ready,
    clear_started,
    confirm,
    continue_step,
    WAIT_SPEAKING,
    draft_changed,
    needs_maintenance,
    run_declared,
    run_direction,
    sequence,
    turn_ended_at,
    wait_ready,
)

LOG_FILE = os.path.join(".tausik", "chat-watch.log")
ERR_FILE = os.path.join(".tausik", "chat-watch.err.log")
LOCK_FILE = os.path.join(".tausik", ".chat-watch.lock")
STOP_FILE = os.path.join(".tausik", ".chat-watch.stop")

IDLE_SECONDS = 45.0  # quiet before the chat counts as "not in use right now"
POLL_SECONDS = 2.0
ESC = "\x1b"
# A run that stopped between steps waits for nobody: the human is away, that is
# why the run was declared. 20 minutes is long enough to sit out a real step and
# short enough that a stall does not cost the night.
ANCHOR_SECONDS = 1200.0
# And it gives up. Three deliveries that moved nothing mean the chat is not
# stalled but finished, and a mechanism typing into an empty queue forever is
# worse than one that stops and says so.
ANCHOR_TRIES = 3
# Hooks may append to the transcript just after the Stop that ended the turn.
# A tolerance for that, and nothing else: the thing it must never swallow is a
# long tool call, which is minutes.
TURN_END_SLACK = 10.0


def log(project_dir: str, message: str) -> None:
    """The watcher has no screen of its own — this file is where it speaks."""
    stamp = time.strftime("%H:%M:%S")
    try:
        with open(os.path.join(project_dir, LOG_FILE), "a", encoding="utf-8") as f:
            f.write(f"{stamp} {message}\n")
    except OSError:
        pass


def should_act(
    percent, threshold, quiet_for, idle_needed=IDLE_SECONDS, busy=False, hard=None
) -> bool:
    """Full window AND provably nobody typing AND no work still running.

    A quiet transcript means the TURN ended, not the work: an agent waiting on
    a test run it started writes nothing for minutes, and the cleanup landed in
    the middle of it. Not knowing counts as a person present; waiting on work is
    bounded by `hard`, or a job that never exits would silence this for good.
    """
    if quiet_for is None:
        return False
    if not (needs_maintenance(percent, threshold) and quiet_for >= idle_needed):
        return False
    if busy and (hard is None or percent is None or percent < hard):
        return False
    return True


def quiet_after_work(quiet, now, busy_since):
    """Silence measured from the end of the WORK, not just of the transcript.

    The transcript stood still for the whole job; counting that as quiet would
    clean the moment it exits, before the result is read.

    The same arithmetic is why a periodic worker was able to block the cleanup
    for good: reset on every busy tick, this can never exceed the gap between
    two wake-ups. Measured at 41s against the 45s required — which is a defect
    of the SELECTION (see `background_pids`), not of this cap.
    """
    if busy_since is None or quiet is None:
        return quiet
    return min(quiet, now - busy_since)


def standing_seconds(ended_at, last_write, now_wall, slack: float = TURN_END_SLACK):
    """How long the run has been STANDING — no turn in flight — or None.

    The flag's mtime says when the host last reported the input line free; the
    transcript's says when anything was last written. Their order is the answer:
    the turn's final message is written just before the Stop hook fires, so a
    standing chat has the flag last. A write AFTER it means a turn is running,
    however quiet it looks.

    `slack` exists because hooks may append their own lines a moment after Stop.
    It is a tolerance for that, not a tuning knob for the 45s question — the gap
    it must never swallow is a long tool call, which is minutes, not seconds.
    """
    if ended_at is None or last_write is None:
        return None
    if last_write > ended_at + slack:
        return None
    return max(0.0, now_wall - ended_at)


def anchor_due(
    now,
    last_at,
    standing_for,
    *,
    busy: bool,
    arming: bool,
    wasted: int,
    gap: float = ANCHOR_SECONDS,
    tries: int = ANCHOR_TRIES,
) -> bool:
    """Whether the run should be handed its direction again.

    This is not a cleanup and cannot replace one: it re-reads nothing and frees
    no window. It answers the other half of the same defect — a chat that sat
    still long enough for its own rules to fade out of the context it kept.

    Every condition is a way of NOT talking over somebody. `standing_for` is the
    one that had to be measured rather than reasoned about: the first version
    asked the transcript how long it had been quiet, and a quiet transcript means
    the TURN ended, not the work — the same trap this file warns about two
    functions up. It cost a delivery into a chat 20 minutes into a background
    command; the host queued it, which is the only reason it was harmless.

    Work in flight is left alone, and an armed cleanup outranks a nudge because
    it re-anchors AND clears, which is strictly more. `last_at` is a floor, not
    the clock: a delivery that failed leaves the flag untouched, and without the
    floor the next tick would try again two seconds later.
    """
    if wasted >= tries or busy or arming:
        return False
    if standing_for is None or standing_for < gap:
        return False
    return (now - last_at) >= gap


def chat_pid(exclude=()) -> int | None:
    """The chat this watcher belongs to."""
    running = [p for p in keys.find_chat_pids() if p not in exclude]
    return running[0] if running else None


def alive(pid) -> bool:
    return bool(pid) and keys.pid_exists(pid)


def stopping(project_dir: str) -> str | None:
    """Why the watcher must stop right now, or None to carry on.

    Asked between cycles AND inside one. A cleanup can spend minutes waiting
    for a mark and retrying, and during that time withdrawing the run used to
    change nothing: the watcher kept typing into a chat it had already been
    released from. Seen live — the run was withdrawn at 23:00, the watcher
    retried /checkpoint at 23:03, and the process had to be killed by hand
    while its keystrokes landed in somebody else's window.
    """
    if os.path.exists(os.path.join(project_dir, STOP_FILE)):
        return "остановлен файлом-стопом"
    if not run_declared(project_dir):
        return "прогон снят — наблюдатель уходит"
    return None


def deliver(project_dir: str, pid: int, command: str, trace: str):
    """Type one command and wait for the mark that proves it ran.

    Retries because a console that accepts the keys may not be reading them:
    right after `/clear` the chat is rebuilding itself, and a write that
    "succeeded" landed nowhere. Without a retry that loss is silent.

    The continuation step is the exception and gets exactly one attempt. It
    starts work that runs for as long as the work takes, so a second attempt
    would land Esc and a duplicate command on an agent mid-edit — the retry
    that protects the other steps is the worst thing that could happen to this
    one.
    """
    attempts = 1 if trace == WAIT_SPEAKING else DELIVERY_ATTEMPTS
    for attempt in range(1, attempts + 1):
        # Asked before every keystroke, not only before the first: waiting for
        # a mark takes minutes, and the human may withdraw the run in between.
        halt = stopping(project_dir)
        if halt:
            log(project_dir, f"{command}: {halt}, подача прервана")
            return False, halt
        # Stale marks answer for the command that has not run yet.
        clear_ready(project_dir)
        clear_started(project_dir)
        # Esc first: the command must not glue itself onto a half-typed draft,
        # then a pause, or the console reads the two writes as one keystroke.
        #
        # Except before the continuation. There the input line is empty — the
        # session has just restarted — so Esc has nothing to clear, and what it
        # does instead is interrupt a turn that is still drawing itself. It
        # also writes the interruption into the transcript, which is the very
        # thing this step waits on: the run of 17:24 logged the continuation as
        # delivered in the same second it was typed, having confirmed itself
        # against its own Esc.
        if trace != WAIT_SPEAKING:
            keys.send_to_console(pid, ESC, submit=False)
            time.sleep(KEY_SETTLE)
        # Measured after Esc and before the text: "the chat answered" has to
        # mean it grew past what it held once everything this watcher does to
        # it was already done.
        baseline = transcript_size(project_dir)
        sent, reason = keys.send_to_console(pid, command)
        if not sent:
            return False, reason
        if confirm(project_dir, trace, baseline=baseline):
            return True, ""
        log(project_dir, f"{command}: следа нет, попытка {attempt} из {attempts}")
    return False, "чат не отозвался — команда не выполнилась"


def run_sequence(project_dir: str, pid: int, cycle: Maintenance) -> bool:
    """Type the commands, each confirmed before the next one goes."""
    spent = session_spent(project_dir)
    steps = sequence(bool(spent), run_direction(project_dir))
    if spent:
        log(project_dir, f"сессия исчерпана ({spent}) — в цикл добавлен /end")
    if not wait_ready(project_dir, cycle.ready_timeout):
        log(project_dir, f"отменено: чат не освободился перед {steps[0][0]}")
        cycle.awaiting_drop = True
        return False
    for command, trace in steps:
        halt = stopping(project_dir)
        if halt:
            log(project_dir, f"{halt}; цикл уборки прерван перед {command}")
            cycle.awaiting_drop = True
            return False
        sent, reason = deliver(project_dir, pid, command, trace)
        if not sent:
            log(project_dir, f"{command} не прошёл: {reason}")
            cycle.awaiting_drop = True
            return False
        log(project_dir, f"{command} выполнен")
    cycle.awaiting_drop = True
    return True


def take_lock(project_dir: str) -> bool:
    """One watcher per project. Two would type over each other."""
    path = os.path.join(project_dir, LOCK_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            owner = int(f.read().strip())
        if keys.pid_exists(owner):
            return False
    except (OSError, ValueError):
        pass
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError:
        return False
    return True


def release_lock(project_dir: str) -> None:
    for name in (LOCK_FILE,):
        try:
            os.unlink(os.path.join(project_dir, name))
        except OSError:
            pass


def watch(
    project_dir: str,
    pid: int | None = None,
    threshold=None,
    transcript: str | None = None,
) -> int:
    """The loop. Ends when the chat does."""
    if not take_lock(project_dir):
        log(project_dir, "наблюдатель уже работает — второй не нужен")
        return 0
    pid = pid or chat_pid()
    if not pid:
        log(project_dir, "чат не найден — наблюдать нечего")
        release_lock(project_dir)
        return 0

    config = load_config(project_dir)
    threshold = threshold if threshold is not None else config["soft_threshold"]
    cycle = Maintenance(threshold=threshold)
    register_own(project_dir)  # a descendant of the chat: else it waits for itself
    log(project_dir, f"старт: pid={pid}, порог {threshold}%")
    clear_ready(project_dir)

    blind, armed_screen, deferred = False, None, False
    anchor_at, anchor_size, anchor_wasted = time.monotonic(), transcript_size(project_dir), 0
    busy, busy_since = False, None
    cpu_seen: dict[int, float] = {}  # last tick's CPU per pid — the busy/idle baseline
    try:
        while alive(pid):
            # The human said stop. Nothing to signal and no PID to get wrong:
            # the declaration is gone, so the watcher is too. The same question
            # is asked inside a running cleanup — see `stopping`.
            halt = stopping(project_dir)
            if halt:
                log(project_dir, halt)
                return 0
            percent = current_percent(project_dir)
            path = transcript_path(project_dir, transcript)
            quiet = idle_seconds(path)
            now = time.monotonic()
            now_wall = time.time()  # the flags below are file times, not ticks
            last_write = None if quiet is None else now_wall - quiet
            # From the process tree, not a marker: a stuck marker here would
            # mean no cleanup ever. Own processes excluded by registry.
            own = own_pids(project_dir, keys.pid_exists)
            # Two questions, not one: what the agent STARTED, then which of it
            # is actually running. A lazily-started language server answers yes
            # to the first forever and no to the second — see `busy_pids`.
            # A heartbeat of the tooling is not work the agent asked for: the
            # transcript's last write says which side of the turn it was born on.
            table = keys.process_table()
            started = background_pids(
                pid,
                table,
                own,
                quiet_since=last_write,
            )
            working, cpu_seen = busy_pids(started, cpu_seen)
            if bool(working) != busy:
                busy = bool(working)
                log(
                    project_dir,
                    f"фоновая работа: {len(working)} процессов "
                    f"({worker_name(working, table)}), уборка ждёт"
                    if busy
                    else "фоновая работа кончилась, отсчёт тишины заново",
                )
            if busy:
                busy_since = now
            else:
                quiet = quiet_after_work(quiet, now, busy_since)
            # Said once, and again only when it changes. This is the state in
            # which the watcher does nothing at all, and a watcher that does
            # nothing silently is indistinguishable from a working one.
            if (quiet is None) != blind:
                blind = quiet is None
                log(
                    project_dir,
                    f"транскрипт не читается ({path or 'путь неизвестен'}) — "
                    "считаю, что человек в чате, уборки не будет"
                    if blind
                    else "транскрипт снова читается — наблюдение восстановлено",
                )

            # Said to the window, not only to the log: both questions the human
            # asked mid-run were about this state, written where nobody looks.
            wstate.observe(
                project_dir,
                blind=blind,
                busy=busy,
                arming=cycle.state != "idle",
                percent=percent,
                threshold=threshold,
                quiet=quiet,
                workers=len(working),
                worker=worker_name(working, table),
            )

            # A cleanup that was asked for. It goes straight to the sequence:
            # everything the countdown below weighs — how full the window is,
            # how long the chat has been quiet, whether a refusal is still in
            # cooldown — the human answered by asking. Sending it through the
            # countdown would do worse than delay it: the request arrives in
            # their own turn, so `human_quiet` is seconds old, and the branch
            # that cancels on a person returning would kill this cleanup on the
            # very next tick and hold the next one off for ten minutes.
            if clean_request.due(project_dir, busy):
                log(project_dir, "уборка по просьбе человека")
                # A countdown already in flight is superseded, and without the
                # refusal cooldown a normal cancel would leave behind.
                cycle.cancel(now=now, quiet_for=0.0)
                clean_request.drop(project_dir)  # before the keys — see `drop`
                run_sequence(project_dir, pid, cycle)
                armed_screen, deferred = None, False
                time.sleep(POLL_SECONDS)
                continue
            if clean_request.requested(project_dir) and not deferred:
                deferred = True  # said once, not every couple of seconds
                log(project_dir, "уборка по просьбе ждёт конца фоновой работы")

            if should_act(
                percent, threshold, quiet, busy=busy, hard=config["hard_threshold"]
            ) and cycle.consider(percent, now):
                armed_screen = keys.console_text(pid)
                log(
                    project_dir,
                    f"окно на {percent}%, тишина {int(quiet)} с — жду {int(ARM_SECONDS)} с",
                )
            if cycle.state != "idle":
                # By the HUMAN's last turn, not the file's mtime: the agent
                # writes to the same transcript, so every step it took read as
                # somebody walking in. Measured on a live run — the cancel
                # landed two seconds after background work started, logged
                # "человек вернулся в чат" with nobody in the room, and cost
                # ten minutes of cooldown on a full window.
                human_quiet = human_idle_seconds(path)
                if (human_quiet is None or human_quiet < IDLE_SECONDS) and cycle.cancel(now=now):
                    # Someone started talking during the countdown; their turn wins.
                    log(project_dir, "отменено: человек вернулся в чат")
            if cycle.due(now):
                # The last look before typing. A draft grows without touching
                # the transcript, so this comparison is the only thing standing
                # between the sequence and an input line still in use.
                if draft_changed(armed_screen, keys.console_text(pid)):
                    cycle.cancel(now=now)
                    log(project_dir, "отменено: экран чата изменился — человек печатает")
                else:
                    run_sequence(project_dir, pid, cycle)
                armed_screen = None
            direction = run_direction(project_dir)
            if direction and anchor_due(
                now,
                anchor_at,
                standing_seconds(turn_ended_at(project_dir), last_write, now_wall),
                busy=busy,
                arming=cycle.state != "idle",
                wasted=anchor_wasted,
            ):
                # Did the PREVIOUS anchor produce anything? The transcript is the
                # only honest answer: a nudge that moved no text moved no work.
                size = transcript_size(project_dir)
                anchor_wasted = anchor_wasted + 1 if size == anchor_size else 0
                if anchor_wasted >= ANCHOR_TRIES:
                    log(project_dir, "якорь не поднял прогон трижды — чат больше не трогаю")
                else:
                    sent, reason = deliver(project_dir, pid, *continue_step(direction))
                    log(
                        project_dir,
                        "якорь: вернул чат к работе" if sent else f"якорь не прошёл: {reason}",
                    )
                anchor_at, anchor_size = time.monotonic(), transcript_size(project_dir)
            time.sleep(POLL_SECONDS)
    finally:
        release_lock(project_dir)
    log(project_dir, "чат закрыт — наблюдатель уходит")
    return 0


def spawn(project_dir: str, pid: int, transcript: str | None = None) -> None:
    """Start a detached watcher. Called from the SessionStart hook, so it must
    return instantly and never hold the chat's startup.

    All three streams are redirected, and stdout/stderr are the ones that
    matter. The host reads a hook's output through a pipe and waits for EOF;
    an unredirected `Popen` hands the child a copy of that pipe's write end,
    and a watcher that lives for hours never lets it close. The hook has long
    exited, the host is still waiting for it — and while it waits, the human's
    input line will not take a word. Measured: EOF never arrived in 8 seconds
    unredirected, and immediately once redirected.

    Errors go to a file rather than to nowhere: a watcher that dies on an
    exception used to leave no trace at all, since its log line is only
    written by code that got the chance to run.
    """
    command = [sys.executable, os.path.abspath(__file__), "--pid", str(pid)]
    if transcript:
        command += ["--transcript", transcript]
    errors: IO[str] | int
    try:
        errors = open(os.path.join(project_dir, ERR_FILE), "a", encoding="utf-8")
    except OSError:
        errors = subprocess.DEVNULL
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,  # nothing to read; an inherited stdin can hang the run
            stdout=subprocess.DEVNULL,
            stderr=errors,
            cwd=project_dir,
            creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | NEW_GROUP
            close_fds=True,
        )
    except (OSError, ValueError) as exc:
        log(project_dir, f"не удалось поднять наблюдателя: {exc}")
    finally:
        if hasattr(errors, "close"):
            errors.close()


def main(argv: list[str]) -> int:
    project_dir = os.getcwd()
    pid, transcript = None, None
    if "--pid" in argv:
        index = argv.index("--pid")
        if len(argv) > index + 1 and argv[index + 1].isdigit():
            pid = int(argv[index + 1])
    if "--transcript" in argv:
        index = argv.index("--transcript")
        if len(argv) > index + 1:
            transcript = argv[index + 1]
    return watch(project_dir, pid, transcript=transcript)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
