"""Start, stop and inspect a run — the only switch this mechanism has.

Everything else in the loop asks one question first: is a run declared? This is
where the answer gets written. Kept apart from the watcher because a person
typing `stop` must not need the watcher to be healthy for the stop to land: the
declaration is a file, and removing it is enough.

    python autoloop_command.py start "очередь задач"
    python autoloop_command.py stop
    python autoloop_command.py status
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "hooks"))

import autoloop_chat_cycle as cycle
import autoloop_clean_request as clean_request
import autoloop_keys as keys
import autoloop_run_state as run_state
import autoloop_watch as watch
import autoloop_window as window


def _int_or_none(value) -> int | None:
    """The chat pid, or None. The optional import answers Any, and a
    function that promises int|None must not pass that through."""
    return None if value is None else int(value)


QUEUE = "очередь задач TAUSIK"
AGENTS_STOP_FILE = os.path.join(".tausik", ".autoloop.stop")


def chat_pid() -> int | None:
    """The chat this command was typed into.

    Walked up from this process, not "the only claude.exe running": with two
    chats open, guessing means declaring a run over somebody else's window.
    """
    try:
        import chat_watch  # type: ignore[import-not-found]  # deployed under hooks/, optional here

        return _int_or_none(chat_watch.owning_chat(os.getpid(), keys.process_table()))
    except ImportError:
        return None


def start(project_dir: str, direction: str) -> str:
    # The two modes share one queue; two runners would take the same tasks from
    # each other. The refusal is symmetric — autoloop_run refuses to start over
    # a declared chat run the same way.
    if run_state.mode(project_dir) == run_state.MODE_AGENTS:
        return (
            "идёт прогон агентами (метка .tausik/.autoloop.run) — "
            "две очереди на один проект дерутся за одни задачи. "
            "Останови его: /auto стоп"
        )
    direction = (direction or "").strip() or QUEUE
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not cycle.start_run(project_dir, direction, now):
        return "не удалось объявить прогон — файл не записался"

    # A request nobody consumed — the run was withdrawn while it waited on
    # background work, or the watcher died holding it. Left alone it belongs to
    # the first tick of THIS run, and wipes a conversation whose owner asked
    # for nothing. Same reason a stuck `.autoloop.stop` is cleared here.
    clean_request.drop(project_dir)

    # Right here, not further down: from this line the run exists, and the
    # window belongs to the run rather than to any of the watcher outcomes
    # below. The chat shows the work as it happens, but not how full the
    # window is or how long the run has gone — that is what the cat carries.
    trouble = window.spawn_overlay(project_dir)
    if trouble:
        # Deliberately not part of the answer: the run is declared either way,
        # and a person on ssh does not need to be told a window failed to open.
        # The trace goes where the mechanism's own words already go.
        watch.log(project_dir, f"окно прогона не поднять: {trouble}")

    if os.path.exists(os.path.join(project_dir, watch.STOP_FILE)):
        return (
            f"прогон объявлен ({direction}), но наблюдатель заглушён файлом "
            f"{watch.STOP_FILE} — удали его, иначе цикл не пойдёт"
        )
    pid = chat_pid()
    if not pid:
        return (
            f"прогон объявлен ({direction}), но чат не опознан — "
            "наблюдатель поднимется на следующем старте сессии"
        )
    if watch.alive(_lock_owner(project_dir)):
        return f"прогон объявлен ({direction}); наблюдатель уже работает"
    watch.spawn(project_dir, pid)
    return f"прогон объявлен ({direction}); наблюдатель поднят для pid={pid}"


def _lock_owner(project_dir: str):
    try:
        with open(os.path.join(project_dir, watch.LOCK_FILE), encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def stop(project_dir: str) -> str:
    """Stop whichever mode is going.

    In the chat that means removing the declaration: the watcher reads it every
    couple of seconds and leaves on its own — no signal, no PID, nothing to get
    wrong. In agents mode the supervisor owns processes, so it is asked to
    finish the iteration it is in rather than killed mid-task.
    """
    if run_state.mode(project_dir) == run_state.MODE_AGENTS:
        try:
            with open(os.path.join(project_dir, AGENTS_STOP_FILE), "w", encoding="utf-8") as f:
                f.write("")
        except OSError as e:
            return f"не удалось попросить прогон агентов остановиться: {e}"
        return (
            "прогон агентами остановится после текущей итерации — "
            "работа, которая идёт, не обрывается"
        )
    if not cycle.run_declared(project_dir):
        return "прогона нет — останавливать нечего"
    cycle.end_run(project_dir)
    clean_request.drop(project_dir)  # an unread request must not outlive its run
    return "прогон снят; наблюдатель уйдёт в течение пары секунд, работа не обрывается"


def clean(project_dir: str) -> str:
    """Ask for the context to be cleaned now instead of when it fills up.

    Deliberately does not ask whether the agent is busy. This process is a
    fresh descendant of the chat, which is exactly what `background_pids`
    counts as work — it would find itself and report "busy" every single time.
    The watcher asks that question from outside, on its own tick; the answer
    here only has to be honest about what that means for the human waiting.
    """
    if run_state.mode(project_dir) == run_state.MODE_AGENTS:
        return (
            "идёт прогон агентами: у каждой итерации свой чистый контекст, "
            "чистить в чате нечего. Итоги — /auto отчёт"
        )
    if not cycle.run_declared(project_dir):
        # Not merely useless — harmful. Nobody would consume the file, and the
        # next `/auto` would find it lying there and wipe a conversation the
        # person had started for something else entirely.
        return (
            "прогона нет — наблюдателя не существует, уборку проводить некому. "
            "Запрос не оставлен: он пролежал бы до следующего /auto и сработал "
            "внезапно. Начать прогон: /auto"
        )
    if not clean_request.request(project_dir):
        return "не удалось оставить запрос на уборку — файл не записался"
    if not watch.alive(_lock_owner(project_dir)):
        return (
            "запрос оставлен, но наблюдатель не поднят: уборка не начнётся, пока "
            "он не появится — он поднимается на старте сессии. Проверить: /auto статус"
        )
    return (
        "уборка пройдёт на ближайшем тике наблюдателя: /checkpoint → /clear → /start. "
        "Порог заполнения, тишина и карантин не спрашиваются — попросили же. "
        "Если сейчас идёт фоновая работа, уборка дождётся её конца: /clear посреди "
        "работы теряет незавершённое"
    )


def status(project_dir: str) -> str:
    if run_state.mode(project_dir) == run_state.MODE_AGENTS:
        return (
            "идёт прогон агентами: очередь разгребают отдельные процессы, "
            "чат свободен. Итоги — /auto отчёт, остановить — /auto стоп"
        )
    run = cycle.read_run(project_dir)
    if not run:
        return "прогона нет: контекст никто не смотрит, чат обычный"
    owner = _lock_owner(project_dir)
    watcher = f"наблюдатель pid={owner}" if watch.alive(owner) else "наблюдатель не поднят"
    started = run.get("started_at") or "время неизвестно"
    return f"прогон идёт: {run['direction']} (с {started}); {watcher}"


def main(argv: list[str]) -> int:
    project_dir = os.getcwd()
    command = argv[0] if argv else "status"
    if command == "start":
        print(start(project_dir, " ".join(argv[1:])))
    elif command == "stop":
        print(stop(project_dir))
    elif command == "status":
        print(status(project_dir))
    elif command == "clean":
        print(clean(project_dir))
    else:
        print("использование: autoloop_command.py start <направление> | stop | status | clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
