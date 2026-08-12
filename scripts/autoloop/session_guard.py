#!/usr/bin/env python3
"""PreToolUse hook — an iteration must not end the session it found open.

The session belongs to the human. An iteration of an autonomous run is a
smaller unit than a session: it takes one task, finishes it, and dies. Closing
the session on the way out was borrowed from how a human ends a working day,
and it cost the human exactly that — a session ended under them, with their
handoff replaced by a machine's.

Telling the agent not to do it is the first layer and the weakest: an
unattended agent that is merely asked will eventually ask itself why not. This
hook is the second, and the supervisor's before/after reading of the session
row is the third — because a rule that only forbids leaves no trace when it
fires, and the point is that an attempt must never pass in silence.

Armed only under autonomy: in an interactive session ending the session is the
human's own business and blocking it would be absurd.

Exit codes: 0 = allow, 2 = block. Receives JSON on stdin.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import autoloop_journal as journal  # noqa: E402

from autoloop import autonomy  # noqa: E402
from autoloop.state import read_task_state  # noqa: E402

MCP_SESSION_END = "mcp__tausik-project__tausik_session_end"
COMMAND_TOOLS = ("Bash", "PowerShell", "mcp__windows-mcp__PowerShell")

# Both spellings the CLI accepts, and only when aimed at tausik: a bare
# "session end" in some other command is not this project's session.
_CLI_SESSION_END_RE = re.compile(r"tausik[^\n|;&]*\bsession[ _-]end\b", re.IGNORECASE)

BLOCK_MESSAGE = (
    "BLOCKED: сессия принадлежит человеку — итерация автономного прогона её не "
    "закрывает.\n"
    "Итог итерации сдаётся так:\n"
    "  1. `task done <slug> --ac-verified` — задача закрывается как обычно.\n"
    '  2. `python .claude/scripts/autoloop_handoff.py write "что сделано, что '
    'дальше" --task <slug>` — handoff уходит в журнал прогона, следующая '
    "итерация получит его в промпте.\n"
    "  3. Просто заверши турн — процесс умрёт сам, супервизор запустит следующую "
    "итерацию.\n"
    "Попытка записана в журнал прогона и попадёт в отчёт."
)


def is_session_end(tool_name: str, tool_input: dict) -> bool:
    """True when this call would close the TAUSIK session."""
    if tool_name == MCP_SESSION_END:
        return True
    if tool_name not in COMMAND_TOOLS:
        return False
    command = tool_input.get("command") or tool_input.get("script") or ""
    if not isinstance(command, str):
        return False
    return bool(_CLI_SESSION_END_RE.search(command))


def _describe_call(tool_name: str, tool_input: dict) -> str:
    if tool_name in COMMAND_TOOLS:
        command = tool_input.get("command") or tool_input.get("script") or ""
        return f"{tool_name}: {str(command)[:120]}"
    return tool_name


def main() -> int:
    if os.environ.get("TAUSIK_SKIP_HOOKS"):
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    if not is_session_end(str(payload.get("tool_name") or ""), tool_input):
        return 0

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    if not autonomy.is_enabled(project_dir):
        return 0  # the human's own session, the human's own call

    _, task_slug = read_task_state(project_dir)
    journal.append_event(
        project_dir,
        journal.EVENT_SESSION_END_BLOCKED,
        tool=_describe_call(str(payload.get("tool_name") or ""), tool_input),
        session_id=payload.get("session_id"),
        task_slug=task_slug,
    )
    print(BLOCK_MESSAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
