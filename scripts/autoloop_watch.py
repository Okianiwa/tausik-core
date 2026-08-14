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

import glob
import json
import os
import re
import subprocess
import sys
import time

import autoloop_keys as keys
from autoloop_presence import idle_seconds, transcript_path
from tausik_utils import tausik_config_path
from autoloop_chat_cycle import (
    ARM_SECONDS,
    DELIVERY_ATTEMPTS,
    KEY_SETTLE,
    Maintenance,
    clear_ready,
    clear_started,
    confirm,
    draft_changed,
    needs_maintenance,
    sequence,
    wait_ready,
)

LOG_FILE = os.path.join(".tausik", "chat-watch.log")
LOCK_FILE = os.path.join(".tausik", ".chat-watch.lock")
STOP_FILE = os.path.join(".tausik", ".chat-watch.stop")

MAX_READING_AGE = 180.0
IDLE_SECONDS = 45.0  # quiet before the chat counts as "not in use right now"
POLL_SECONDS = 2.0
ESC = "\x1b"

STATUS_TIMEOUT = 30.0
CREATE_NO_WINDOW = 0x08000000
# Two budgets, not one. The clock is the famous limit, but the capacity gate is
# the one that actually refuses work: `task start` blocks on remaining calls
# long before the 180 minutes are up.
CLOCK_LINE = re.compile(r"active\s+(\d+)m\s*/\s*(\d+)m")
CAPACITY_LINE = re.compile(r"Capacity:\s*(\d+)/(\d+)\s+used.*?(-?\d+)\s+remaining")


def log(project_dir: str, message: str) -> None:
    """The watcher has no screen of its own — this file is where it speaks."""
    stamp = time.strftime("%H:%M:%S")
    try:
        with open(os.path.join(project_dir, LOG_FILE), "a", encoding="utf-8") as f:
            f.write(f"{stamp} {message}\n")
    except OSError:
        pass


def reading(project_dir: str, max_age=MAX_READING_AGE, now=None):
    """This session's window fill, or None.

    A reading older than `max_age` belongs to somebody else's run: a stale 33%
    once armed a cleanup on a chat that had said one word.
    """
    now = time.time() if now is None else now
    newest, newest_mtime = None, -1.0
    for path in glob.glob(os.path.join(project_dir, ".tausik", "autoloop", "*.json")):
        try:
            mtime = os.path.getmtime(path)
            if mtime <= newest_mtime or (now - mtime) > max_age:
                continue
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and "percent" in data:
            newest, newest_mtime = data.get("percent"), mtime
    return newest


def should_act(percent, threshold, quiet_for, idle_needed=IDLE_SECONDS) -> bool:
    """Full window AND provably nobody typing. Either alone is not enough:
    acting on a live conversation wipes it mid-sentence, and acting without a
    measurement is acting blind. Not knowing counts as a live conversation."""
    if quiet_for is None:
        return False
    return needs_maintenance(percent, threshold) and quiet_for >= idle_needed


def chat_pid(exclude=()) -> int | None:
    """The chat this watcher belongs to."""
    running = [p for p in keys.find_chat_pids() if p not in exclude]
    return running[0] if running else None


def alive(pid) -> bool:
    return bool(pid) and keys.pid_exists(pid)


def deliver(project_dir: str, pid: int, command: str, trace: str):
    """Type one command and wait for the mark that proves it ran.

    Retries because a console that accepts the keys may not be reading them:
    right after `/clear` the chat is rebuilding itself, and a write that
    "succeeded" landed nowhere. Without a retry that loss is silent.
    """
    for attempt in range(1, DELIVERY_ATTEMPTS + 1):
        # Stale marks answer for the command that has not run yet.
        clear_ready(project_dir)
        clear_started(project_dir)
        # Esc first: the command must not glue itself onto a half-typed draft,
        # then a pause, or the console reads the two writes as one keystroke.
        keys.send_to_console(pid, ESC, submit=False)
        time.sleep(KEY_SETTLE)
        sent, reason = keys.send_to_console(pid, command)
        if not sent:
            return False, reason
        if confirm(project_dir, trace):
            return True, ""
        log(
            project_dir,
            f"{command}: следа нет, попытка {attempt} из {DELIVERY_ATTEMPTS}",
        )
    return False, "чат не отозвался — команда не выполнилась"


def status_text(project_dir: str):
    """What the CLI says about the project, or None.

    Read through the CLI, like everything else here — the database is not this
    process's to open, and the limits it would have to compute by hand already
    account for `session extend`.
    """
    cli = os.path.join(os.path.dirname(os.path.abspath(__file__)), "project.py")
    try:
        out = subprocess.run(
            [sys.executable, cli, "status"],
            stdin=subprocess.DEVNULL,  # nothing to read; an inherited stdin can hang the run
            cwd=project_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=STATUS_TIMEOUT,
            creationflags=CREATE_NO_WINDOW,  # or the desktop blinks every cycle
            env={**os.environ, "PYTHONUTF8": "1"},
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return out.stdout or ""


def session_spent(project_dir: str):
    """Which budget has run out, named for the log — or None while there is room.

    Anything unreadable answers "there is room": closing a session that still
    had some costs the human their working history for nothing, while a
    session closed one cycle late costs nothing at all.
    """
    text = status_text(project_dir)
    if text is None:
        return None
    clock = CLOCK_LINE.search(text)
    if clock and int(clock.group(1)) >= int(clock.group(2)):
        return f"время {clock.group(1)}/{clock.group(2)} мин"
    room = CAPACITY_LINE.search(text)
    if room and (int(room.group(1)) >= int(room.group(2)) or int(room.group(3)) <= 0):
        return f"ёмкость {room.group(1)}/{room.group(2)}, свободно {room.group(3)}"
    return None


def run_sequence(project_dir: str, pid: int, cycle: Maintenance) -> bool:
    """Type the commands, each confirmed before the next one goes."""
    spent = session_spent(project_dir)
    steps = sequence(bool(spent))
    if spent:
        log(project_dir, f"сессия исчерпана ({spent}) — в цикл добавлен /end")
    if not wait_ready(project_dir, cycle.ready_timeout):
        log(project_dir, f"отменено: чат не освободился перед {steps[0][0]}")
        cycle.awaiting_drop = True
        return False
    for command, trace in steps:
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

    threshold = threshold if threshold is not None else soft_threshold(project_dir)
    cycle = Maintenance(threshold=threshold)
    log(project_dir, f"старт: pid={pid}, порог {threshold}%")
    clear_ready(project_dir)

    blind, armed_screen = False, None
    try:
        while alive(pid):
            if os.path.exists(os.path.join(project_dir, STOP_FILE)):
                log(project_dir, "остановлен файлом-стопом")
                return 0
            percent = reading(project_dir)
            path = transcript_path(project_dir, transcript)
            quiet = idle_seconds(path)
            now = time.monotonic()

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

            if should_act(percent, threshold, quiet) and cycle.consider(percent, now):
                armed_screen = keys.console_text(pid)
                log(
                    project_dir,
                    f"окно на {percent}%, тишина {int(quiet)} с — жду {int(ARM_SECONDS)} с",
                )
            if (
                cycle.state != "idle"
                and (quiet is None or quiet < IDLE_SECONDS)
                and cycle.cancel(now=now)
            ):
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
            time.sleep(POLL_SECONDS)
    finally:
        release_lock(project_dir)
    log(project_dir, "чат закрыт — наблюдатель уходит")
    return 0


def soft_threshold(project_dir: str, default=30):
    try:
        with open(tausik_config_path(project_dir), encoding="utf-8") as f:
            return json.load(f).get("autoloop", {}).get("soft_threshold", default)
    except (OSError, ValueError, AttributeError):
        return default


def spawn(project_dir: str, pid: int, transcript: str | None = None) -> None:
    """Start a detached watcher. Called from the SessionStart hook, so it must
    return instantly and never hold the chat's startup."""
    command = [sys.executable, os.path.abspath(__file__), "--pid", str(pid)]
    if transcript:
        command += ["--transcript", transcript]
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,  # nothing to read; an inherited stdin can hang the run
            cwd=project_dir,
            creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | NEW_GROUP
            close_fds=True,
        )
    except (OSError, ValueError) as exc:
        log(project_dir, f"не удалось поднять наблюдателя: {exc}")


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
