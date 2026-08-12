"""Decide whether this process is a supervised headless run — and act on it.

Autonomy is deliberately hard to switch on by accident. An exported
TAUSIK_AUTONOMY that lingers in someone's shell must not turn their next
interactive session into one that commits, pushes and closes itself. So the
flag alone is not enough: a run marker written by the supervisor has to be
present too, and the process must not be attached to a terminal.

What autonomy does NOT relax, by design:
  * QG-0 / QG-2 — a task still needs goal + AC, and still needs a green verify.
  * secret_scan, scope_write_gate, task_gate — untouched.
  * bash_firewall — force-push, reset --hard and friends stay blocked.
Autonomy only removes the *human confirmation* step for git, and it does that
through the library's own single-use push ticket rather than by patching the
gate: `.tausik-lib/` is a git checkout of the fork and edits there are lost on
the next `/fab update`.
"""

from __future__ import annotations

import os
import subprocess
import sys

RUN_MARKER = ".autoloop.run"

REASON_DISABLED = "flag_absent"
REASON_NO_MARKER = "no_run_marker"
REASON_INTERACTIVE = "interactive_terminal"
REASON_HOOKS_DISABLED = "hooks_disabled"


def run_marker_path(project_dir: str) -> str:
    return os.path.join(project_dir, ".tausik", RUN_MARKER)


def status(project_dir: str) -> tuple[bool, str | None]:
    """(enabled, reason_when_disabled).

    TAUSIK_SKIP_HOOKS is treated as disqualifying rather than convenient: an
    unattended agent with the quality gates switched off is exactly the thing
    this design refuses to build.
    """
    if os.environ.get("TAUSIK_AUTONOMY") != "1":
        return False, REASON_DISABLED
    if os.environ.get("TAUSIK_SKIP_HOOKS"):
        return False, REASON_HOOKS_DISABLED
    if not os.path.exists(run_marker_path(project_dir)):
        return False, REASON_NO_MARKER
    if _attached_to_terminal():
        return False, REASON_INTERACTIVE
    return True, None


def is_enabled(project_dir: str) -> bool:
    return status(project_dir)[0]


def _attached_to_terminal() -> bool:
    """True when a human is plausibly watching this process directly."""
    for stream in (sys.stdin, sys.stdout):
        try:
            if stream is not None and stream.isatty():
                return True
        except (ValueError, AttributeError):
            continue
    return False


def warn_if_misconfigured(project_dir: str) -> str | None:
    """Explain a flag that is set but not in effect. None when nothing to say."""
    if os.environ.get("TAUSIK_AUTONOMY") != "1":
        return None
    enabled, reason = status(project_dir)
    if enabled:
        return None
    messages = {
        REASON_NO_MARKER: (
            "TAUSIK_AUTONOMY=1 выставлена, но метки прогона нет — автономный режим "
            "НЕ активирован. Запускай цикл через autoloop.py, а не выставляй "
            "переменную вручную."
        ),
        REASON_INTERACTIVE: (
            "TAUSIK_AUTONOMY=1 выставлена в интерактивном терминале — автономный "
            "режим НЕ активирован. Переменная, забытая в оболочке, иначе позволила "
            "бы сессии коммитить и завершать себя без спроса."
        ),
        REASON_HOOKS_DISABLED: (
            "TAUSIK_AUTONOMY=1 вместе с TAUSIK_SKIP_HOOKS — автономный режим НЕ "
            "активирован. Без гейтов автономная работа не запускается."
        ),
    }
    return messages.get(reason)


def create_run_marker(project_dir: str, pid: int) -> bool:
    """Claim autonomy for this run. False when the marker could not be written."""
    path = run_marker_path(project_dir)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(pid))
    except OSError:
        return False
    return True


def remove_run_marker(project_dir: str) -> None:
    try:
        os.unlink(run_marker_path(project_dir))
    except OSError:
        pass


def issue_push_ticket(project_dir: str, ttl: int = 60, timeout: int = 15) -> bool:
    """Mint the library's single-use push ticket so an unattended push can land.

    This is the sanctioned path — git_push_gate documents that an agent may
    call `tausik push-ok` itself; the ticket is a discipline rail, not a
    firewall. Bounded TTL and HEAD binding still apply, so a ticket minted here
    cannot authorise a later, different push.
    """
    if not is_enabled(project_dir):
        return False
    cli = _tausik_cli(project_dir)
    if not cli:
        return False
    try:
        result = subprocess.run(
            [cli, "push-ok", "--ttl", str(ttl)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=project_dir,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def _tausik_cli(project_dir: str) -> str | None:
    candidates = []
    if sys.platform == "win32":
        candidates.append(os.path.join(project_dir, ".tausik", "tausik.cmd"))
    candidates.append(os.path.join(project_dir, ".tausik", "tausik"))
    for path in candidates:
        if os.path.exists(path):
            return path
    return None
