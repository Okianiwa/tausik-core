#!/usr/bin/env python3
"""Supervisor for the autonomous execution loop.

Runs OUTSIDE Claude Code and is the only component that can actually clear the
context: `/clear` is a TUI command no agent or hook can invoke, so each
iteration is a fresh `claude -p` process instead. What survives between them is
not conversation but state — tasks, plan steps, handoff — in .tausik/tausik.db.

    python .claude/scripts/autoloop.py run
    python .claude/scripts/autoloop.py run --max-iterations 5 --dry-run

Brakes (kill-switch, iteration cap, no-progress detector, crash streak) live in
autoloop_brakes.py and are consulted before every iteration.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import autoloop_handoff as handoff  # noqa: E402
import autoloop_journal as journal  # noqa: E402

from autoloop import autonomy  # noqa: E402
from autoloop.state import (  # noqa: E402
    load_config,
    prune_state_files,
    read_session_row,
    read_state,
)
import autoloop_git  # noqa: E402
from autoloop_limits import session_spent  # noqa: E402
from autoloop_child import (  # noqa: E402
    build_prompt,
    claude_command,
    extract_tokens,
    git_head,
    new_commits,
    run_iteration,
)


EXIT_OK = 0
EXIT_STOPPED = 1

# build_prompt/extract_tokens live in autoloop_child but are addressed through
# this module by callers and tests that knew them here before the split. Named
# explicitly so the re-export reads as intentional and ruff stops calling them
# dead imports.
__all__ = [
    "build_prompt",
    "claude_command",
    "extract_tokens",
    "loop",
    "main",
    "pick_next_task",
    "report_session_closure",
]


def log(message: str) -> None:
    print(message, flush=True)


def tausik_cli(project_dir: Path) -> str | None:
    for name in ("tausik.cmd", "tausik") if sys.platform == "win32" else ("tausik",):
        candidate = project_dir / ".tausik" / name
        if candidate.exists():
            return str(candidate)
    return None


def _run_cli(project_dir: Path, args: list[str], timeout: int = 30) -> str:
    cli = tausik_cli(project_dir)
    if not cli:
        return ""
    try:
        result = subprocess.run(
            [cli, *args],
            stdin=subprocess.DEVNULL,  # nothing to read; an inherited stdin can hang the run
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(project_dir),
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def list_task_slugs(project_dir: Path, status: str) -> list[str]:
    """Slugs with the given status, in listing order.

    Parses the CLI table rather than touching sqlite: the DB is the library's
    to shape, and a schema change should surface as an empty list, not as a
    supervisor that silently works on the wrong column.
    """
    out = _run_cli(project_dir, ["task", "list", "--status", status])
    slugs = []
    for line in out.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("slug", "-", "(")):
            continue
        slug = stripped.split()[0]
        if slug and not slug.startswith("("):
            slugs.append(slug)
    return slugs


def pick_next_task(project_dir: Path) -> str | None:
    """An already-active task first — it is someone's unfinished business."""
    for status in ("active", "planning"):
        slugs = list_task_slugs(project_dir, status)
        if slugs:
            return slugs[0]
    return None


def task_status_snapshot(project_dir: Path) -> dict[str, str]:
    """{slug: status} across the statuses the loop can act on."""
    snapshot = {}
    for status in ("active", "planning", "blocked", "review", "done"):
        for slug in list_task_slugs(project_dir, status):
            snapshot[slug] = status
    return snapshot


def report_session_closure(
    project_dir: Path, session_before: tuple[int | None, str | None], task_slug: str
) -> bool:
    """Journal the human's session being closed by an iteration. True if it was.

    Only the transition open -> closed counts. A session that was already
    closed before the iteration is the ordinary case — the run does not open
    one — and an iteration is not blamed for a state it inherited.
    """
    row_id, ended_before = session_before
    if row_id is None or ended_before is not None:
        return False
    _, ended_after = read_session_row(str(project_dir))
    if ended_after is None:
        return False
    journal.append_event(
        str(project_dir),
        journal.EVENT_SESSION_CLOSED,
        task_slug=task_slug,
        session_row=row_id,
        ended_at=ended_after,
    )
    log(
        f"[autoloop]   ВНИМАНИЕ: итерация закрыла сессию #{row_id} — "
        "запрет обойдён, запись в журнале"
    )
    return True


def loop(args: argparse.Namespace) -> int:
    from autoloop_brakes import BrakeState, check_brakes

    project_dir = PROJECT_DIR
    config = load_config(str(project_dir))
    max_iterations = args.max_iterations or config["max_iterations"]

    if not tausik_cli(project_dir):
        log("[autoloop] TAUSIK CLI не найден — прогон невозможен")
        return EXIT_STOPPED

    if os.path.exists(autonomy.run_marker_path(str(project_dir))):
        log(
            "[autoloop] прогон уже идёт (есть метка .tausik/.autoloop.run). "
            "Останови его — `touch .tausik/.autoloop.stop` — или удали метку, "
            "если процесс умер, не убрав её за собой."
        )
        return EXIT_STOPPED

    if not pick_next_task(project_dir):
        log("[autoloop] очередь пуста — делать нечего")
        return EXIT_OK

    if not autonomy.create_run_marker(str(project_dir), os.getpid()):
        log("[autoloop] не удалось выставить метку прогона — прогон отменён")
        return EXIT_STOPPED

    pruned = prune_state_files(str(project_dir))
    if pruned:
        log(f"[autoloop] удалено старых файлов состояния: {pruned}")

    # An iteration still open when a new run starts belongs to a supervisor
    # that died — label it now rather than leaving it ambiguous forever.
    orphans = journal.mark_orphans_crashed(str(project_dir))
    if orphans:
        log(f"[autoloop] в журнале {orphans} незакрытых итераций — помечены как падения")

    brakes = BrakeState.from_config(
        config,
        max_iterations=max_iterations,
        max_idle_iterations=args.max_idle,
        max_crash_streak=args.max_crashes,
    )
    exit_code = EXIT_OK
    try:
        while True:
            verdict = check_brakes(project_dir, brakes)
            if verdict.stop:
                log(f"[autoloop] стоп: {verdict.reason}")
                exit_code = EXIT_OK if verdict.clean else EXIT_STOPPED
                break

            # Asked before the task is picked, not after the iteration failed.
            # A session with no budget left refuses `task start`, and the child
            # spends its turn discovering that — the run then looks like a
            # string of iterations that did nothing, with the reason visible
            # only inside conversations nobody kept.
            spent = session_spent(str(project_dir))
            if spent:
                journal.append_event(str(project_dir), journal.EVENT_SESSION_EXHAUSTED, spent=spent)
                log(f"[autoloop] стоп: у сессии кончилась {spent} — задачи не стартуют")
                exit_code = EXIT_OK
                break

            task_slug = pick_next_task(project_dir)
            if not task_slug:
                log("[autoloop] очередь пуста — цикл завершён")
                break

            brakes.iteration += 1
            before = task_status_snapshot(project_dir)
            log(f"[autoloop] итерация {brakes.iteration}: {task_slug}")

            if args.dry_run:
                log(
                    "[autoloop] dry-run, команда: "
                    + " ".join(claude_command("<prompt>", project_dir, args.model, args.git_mode))
                )
                break

            entry = journal.open_iteration(str(project_dir), brakes.iteration, task_slug, before)
            head_before = git_head(project_dir)
            session_before = read_session_row(str(project_dir))
            result = run_iteration(
                project_dir,
                task_slug,
                config,
                args.model,
                args.timeout,
                git_mode=args.git_mode,
                direction=args.direction,
                handoff=handoff.last_text(str(project_dir)),
            )
            report_session_closure(project_dir, session_before, task_slug)
            after = task_status_snapshot(project_dir)
            # Read the reading of THIS child, named by the session id it
            # reported. Without the id there is no honest answer — a parallel
            # session's file is not a fallback, it is a different conversation.
            child_session = result.get("session_id")
            state = read_state(str(project_dir), child_session) if child_session else {}
            unmeasured = None if child_session else "session_unknown"
            journal.close_iteration(
                str(project_dir),
                entry,
                exit_reason=(
                    result["error"] or state.get("exit_kind") or unmeasured or "completed"
                ),
                percent_at_exit=state.get("percent"),
                status_after=after.get(task_slug),
                commits=new_commits(project_dir, head_before),
                cost_usd=result.get("cost_usd") or 0.0,
                tokens=result.get("tokens") or {},
            )
            brakes.record(
                task_slug=task_slug,
                crashed=result["returncode"] != 0 or bool(result["error"]),
                progressed=before != after,
                cost_usd=result.get("cost_usd") or 0.0,
            )
            log(
                f"[autoloop]   код={result['returncode']} "
                f"контекст={state.get('percent')}% "
                f"выход={state.get('exit_kind') or '—'} "
                f"стоимость=${result.get('cost_usd') or 0:.4f}"
                + (f" ошибка={result['error']}" if result["error"] else "")
            )
    finally:
        # Must survive crashes and Ctrl+C: a stale marker would leave the next
        # interactive session believing it is an autonomous run.
        autonomy.remove_run_marker(str(project_dir))

    log(
        f"[autoloop] итераций: {brakes.iteration}, "
        f"суммарная стоимость: ${brakes.total_cost_usd:.4f}"
    )
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Автономный цикл выполнения задач TAUSIK")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="запустить цикл")
    run.add_argument("--max-iterations", type=int, default=None)
    run.add_argument(
        "--max-idle",
        type=int,
        default=None,
        help="итераций подряд без прогресса до остановки",
    )
    run.add_argument("--max-crashes", type=int, default=None, help="падений подряд до остановки")
    run.add_argument("--model", default=None)
    run.add_argument(
        "--git-mode",
        choices=autoloop_git.GIT_MODES,
        default=autoloop_git.GIT_FULL,
        help="full — коммит и пуш; commit — только коммит; off — git не трогать",
    )
    run.add_argument(
        "--direction",
        default="",
        help="направление работы, добавляется в промпт итерации",
    )
    run.add_argument("--timeout", type=int, default=3600, help="таймаут одной итерации, сек")
    run.add_argument("--dry-run", action="store_true", help="показать команду и выйти")

    sub.add_parser("report", help="сводка последнего прогона")
    sub.add_parser("watch", help="живой дашборд прогона")
    sub.add_parser("overlay", help="плавающее окно поверх других окон")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return loop(args)
    if args.command == "watch":
        import autoloop_screen

        try:
            return autoloop_screen.run_dashboard(str(PROJECT_DIR), load_config(str(PROJECT_DIR)))
        except KeyboardInterrupt:
            # textual restores the terminal on its own teardown; this only keeps
            # Ctrl+C from printing a traceback over the restored screen.
            return EXIT_OK
    if args.command == "overlay":
        import autoloop_overlay

        try:
            return autoloop_overlay.run_overlay(str(PROJECT_DIR), load_config(str(PROJECT_DIR)))
        except KeyboardInterrupt:
            return EXIT_OK
    if args.command == "report":
        # An empty journal is a normal state, not a failure: it just means
        # nothing has run yet.
        log(journal.format_report(str(PROJECT_DIR)))
        return EXIT_OK
    return EXIT_STOPPED


if __name__ == "__main__":
    sys.exit(main())
