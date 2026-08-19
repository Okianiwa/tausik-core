#!/usr/bin/env python3
"""Stop hook — end the session when the context window is running out.

The agent cannot clear its own context: `/clear` is a TUI command and no hook
can invoke it. What a Stop hook *can* do is refuse to let the turn end and hand
the agent an instruction — so the exit is cooperative. The agent writes a
handoff into the run journal and stops; the process then exits normally, and
the supervisor outside starts a fresh one. A dead process is the only real
clear.

The handoff goes to the run journal and not to the TAUSIK session, and the
session is left open: it belongs to the human, and an iteration that ends it
takes their working day down with its own. See autoloop/session_guard.py.

Two thresholds, because "context is filling up" and "context is nearly gone"
call for different behaviour:

  soft (30%)  — only at a seam, when the active task's plan is fully ticked
                off. Nothing is half-written, so recycling costs nothing.
  hard (75%)  — regardless of task state. Losing the remaining window costs
                more than interrupting the work, so the agent is told to
                записать handoff describing exactly where it stopped.

Only armed under TAUSIK_AUTONOMY=1. In an interactive session it degrades to a
stderr note: blocking Stop there would hijack a turn the human is watching.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoloop import autonomy  # noqa: E402
from autoloop.sensor import build_payload  # noqa: E402
from autoloop.state import STATE_COMPLETE, load_config, read_state, write_state  # noqa: E402

EXIT_SOFT = "soft"
EXIT_HARD = "hard"

_HANDOFF_COMMAND = 'python .claude/scripts/autoloop_handoff.py write "<текст>" --task {task}'

_SOFT_INSTRUCTION = (
    "[autoloop] Контекст заполнен на {percent}% (порог {threshold}%), а задача «{task}» "
    "доведена до конца — все шаги плана закрыты. Сворачивайся СЕЙЧАС, не начиная новую работу:\n"
    "1. Убедись, что задача закрыта через `tausik verify` + `task done --ac-verified`.\n"
    "2. Запиши handoff для следующей итерации — что сделано и какая задача следующая:\n"
    "   " + _HANDOFF_COMMAND + "\n"
    "3. Заверши турн. Сессию НЕ закрывай — она принадлежит человеку, процесс умрёт сам.\n"
    "Ничего другого в этом турне не делай — процесс будет перезапущен с чистым контекстом."
)

_HARD_INSTRUCTION = (
    "[autoloop] Контекст заполнен на {percent}% (аварийный порог {threshold}%). "
    "Окна почти не осталось — работу нужно прервать, даже если задача «{task}» не завершена.\n"
    "1. НЕ начинай новых правок и не запускай тяжёлых команд.\n"
    "2. Запиши handoff максимально конкретно: на каком шаге плана остановился, "
    "какие файлы уже изменены, что проверить следующей итерации:\n"
    "   " + _HANDOFF_COMMAND + "\n"
    "3. Залогируй прогресс в задачу (`task log`) и заверши турн. Сессию НЕ закрывай.\n"
    "Следующая итерация продолжит с этого места с чистым контекстом."
)


# Не утверждает, что задача закончена: в чате эта просьба приходит и посреди
# работы — окно заполняется, пока агент берёт следующее, и другого канала до
# него нет. Наблюдатель снаружи ждёт 45 секунд тишины, которых сплошная работа
# не даёт.
_SOFT_CHAT = (
    "[autoloop] Контекст заполнен на {percent}% (порог {threshold}%) — окно скоро будет "
    "очищено. Доведи задачу «{task}» до логического конца и остановись, новую не начинай:\n"
    "1. Закрой её штатно: `task update --relevant-files`, `tausik verify`, "
    "`task done --ac-verified`. Если закрыть нельзя — запиши состояние в `task log` "
    "и заблокируй с причиной (`task block`).\n"
    "2. Выполни `/checkpoint` — handoff в БД переживёт очистку, переписка нет.\n"
    "3. Заверши турн и ничего больше не делай. Контекст очистит наблюдатель снаружи "
    "и вернёт тебя к работе — процесс завершать НЕ нужно, сессию НЕ закрывай."
)

_HARD_CHAT = (
    "[autoloop] Контекст заполнен на {percent}% (аварийный порог {threshold}%). "
    "Окна почти не осталось — работу нужно прервать, даже если задача «{task}» не завершена.\n"
    "1. НЕ начинай новых правок и не запускай тяжёлых команд.\n"
    "2. Залогируй в задачу (`task log`), на каком шаге плана остановился, какие файлы уже "
    "изменены и что проверить дальше.\n"
    "3. Выполни `/checkpoint` и заверши турн. Очистку проведёт наблюдатель снаружи; "
    "процесс завершать НЕ нужно, сессию НЕ закрывай."
)


def run_declared(project_dir: str) -> bool:
    """Did the human declare a run in this chat?

    Import-local: a Stop hook that dies on an import kills the turn it was
    supposed to end politely.
    """
    try:
        import autoloop_chat_cycle

        return autoloop_chat_cycle.run_declared(project_dir)
    except ImportError:
        return False


def decide(state: dict, config: dict, interactive: bool = False) -> str | None:
    """Which exit, if any, this state calls for. None = let the turn end.

    The soft threshold asks for a wind-down, and whom it may interrupt differs
    by mode. A headless iteration dies after its task regardless, so cutting
    one off mid-task buys nothing and costs the work in flight — there the
    request waits for a task already closed.

    A declared run inside a live chat has no such boundary. The window fills
    while the agent takes on the next thing, and this hook — firing at the end
    of every turn — is the only channel that reaches it: the watcher outside
    arms on 45 seconds of silence, which continuous work never produces. So in
    a chat the soft threshold asks however far the task has got.
    """
    percent = state.get("percent")
    if not isinstance(percent, (int, float)):
        return None  # unmeasurable context is not evidence of a full one
    if percent >= config["hard_threshold"]:
        return EXIT_HARD
    if percent >= config["soft_threshold"] and (
        interactive or state.get("task_state") == STATE_COMPLETE
    ):
        return EXIT_SOFT
    return None


def build_instruction(kind: str, state: dict, config: dict, interactive: bool = False) -> str:
    """The instruction handed to the agent. Two dialects, one decision.

    Headless ends by dying: the supervisor starts a fresh process, so the
    handoff has to go into the run journal. In a declared run inside a live
    chat nothing dies — the watcher outside runs `/clear` and hands the work
    back — so the handoff belongs in the database, where `/checkpoint` puts it,
    and telling the agent to exit would take the human's window down with it.
    """
    if interactive:
        template = _HARD_CHAT if kind == EXIT_HARD else _SOFT_CHAT
    else:
        template = _HARD_INSTRUCTION if kind == EXIT_HARD else _SOFT_INSTRUCTION
    threshold = config["hard_threshold"] if kind == EXIT_HARD else config["soft_threshold"]
    return template.format(
        percent=state.get("percent"),
        threshold=int(threshold),
        task=state.get("task_slug") or "—",
    )


def autonomy_enabled(project_dir: str) -> bool:
    """Delegated so the flag alone can never arm the guard — see autonomy.py."""
    return autonomy.is_enabled(project_dir)


def main() -> int:
    if os.environ.get("TAUSIK_SKIP_HOOKS"):
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0

    # The harness sets this on the turn it re-runs after a block. Honouring it
    # is what keeps a block from becoming an infinite loop.
    if payload.get("stop_hook_active"):
        return 0

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    if not os.path.isdir(os.path.join(project_dir, ".tausik")):
        return 0

    # Measured here rather than read from the sensor's file: relying on hook
    # ordering would make this silently wrong the day someone reorders them.
    config = load_config(project_dir)
    session_id = str(payload.get("session_id") or "")
    state = build_payload(project_dir, payload.get("transcript_path") or "", session_id)
    # Two ways to be armed. Headless autonomy is one. The other is a run the
    # human declared in this very chat: without it the guard stays silent and
    # the window fills to the brim, because the watcher outside waits for a
    # quiet that never comes — the transcript keeps growing from the agent's
    # own work. Asking the agent to wrap up is the only signal that reaches it
    # mid-flight.
    interactive = not autonomy_enabled(project_dir) and run_declared(project_dir)
    kind = decide(state, config, interactive=interactive)
    if kind is None:
        return 0

    if not (autonomy_enabled(project_dir) or interactive):
        note = autonomy.warn_if_misconfigured(project_dir)
        print(
            f"[autoloop] контекст {state['percent']}% — в автономном режиме "
            f"здесь была бы перезагрузка сессии (порог "
            f"{int(config['soft_threshold'])}%/{int(config['hard_threshold'])}%)"
            + (f"\n[autoloop] {note}" if note else ""),
            file=sys.stderr,
        )
        return 0

    # Scoped to this session: a block fired in a parallel conversation says
    # nothing about whether this one has already been asked to leave. This is
    # now the only guard against asking twice — the open-session check that
    # used to back it up was removed with the session-closing exit it assumed:
    # with the session left open on purpose, "still open" stopped meaning
    # "the agent has not left yet" and started meaning nothing at all.
    previous = read_state(project_dir, session_id)
    if previous.get("exit_requested"):
        return 0  # already asked; the agent is on its way out

    state["exit_requested"] = True
    state["exit_kind"] = kind
    write_state(project_dir, session_id, state)

    instruction = build_instruction(kind, state, config, interactive)
    print(json.dumps({"decision": "block", "reason": instruction}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
