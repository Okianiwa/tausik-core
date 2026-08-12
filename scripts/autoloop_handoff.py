"""Handoff between iterations of a run — kept apart from the human's session.

TAUSIK stores a handoff on the session row, and a session belongs to whoever
opened it. While iterations closed the session to leave their notes there, two
things followed from one mistake: the human came back to a session that had
been ended under them, and their handoff had been overwritten by a machine's.

So the run gets its own place to leave notes: the run journal. It is a per-run
entity, which is what an iteration actually hands over to — the next iteration,
not the next human.

    python .claude/scripts/autoloop_handoff.py write "что сделано, что дальше" --task slug
    python .claude/scripts/autoloop_handoff.py show
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_DIR / ".claude" / "scripts"))

import autoloop_journal as journal  # noqa: E402


def write(project_dir: str, text: str, task_slug: str | None = None) -> bool:
    """Leave a note for the next iteration. False when the journal is unwritable."""
    text = (text or "").strip()
    if not text:
        return False
    return journal.append_event(
        project_dir,
        journal.EVENT_HANDOFF,
        text=text,
        task_slug=task_slug or None,
    )


def last(project_dir: str) -> dict | None:
    """The most recent handoff event, or None when the run has left none yet."""
    events = journal.read_events(project_dir, journal.EVENT_HANDOFF)
    return events[-1] if events else None


def last_text(project_dir: str) -> str:
    """Just the text of the last handoff; empty string when there is none."""
    event = last(project_dir)
    if not event:
        return ""
    text = event.get("text")
    return text.strip() if isinstance(text, str) else ""


def format_last(project_dir: str) -> str:
    event = last(project_dir)
    if not event:
        return "[autoloop] предыдущих итераций в этом прогоне нет — журнал без handoff"
    task = event.get("task_slug") or "—"
    return f"[autoloop] handoff предыдущей итерации ({event.get('at')}, задача {task}):\n{event.get('text')}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Handoff между итерациями прогона")
    sub = parser.add_subparsers(dest="command", required=True)

    write_cmd = sub.add_parser("write", help="записать итог итерации")
    write_cmd.add_argument("text")
    write_cmd.add_argument("--task", default=None)
    sub.add_parser("show", help="показать handoff предыдущей итерации")

    args = parser.parse_args(argv)
    project_dir = str(PROJECT_DIR)

    if args.command == "write":
        if not write(project_dir, args.text, args.task):
            print("[autoloop] handoff не записан: пустой текст или журнал недоступен")
            return 1
        print("[autoloop] handoff записан в журнал прогона")
        return 0

    print(format_last(project_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
