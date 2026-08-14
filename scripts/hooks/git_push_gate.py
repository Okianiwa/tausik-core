#!/usr/bin/env python3
"""PreToolUse hook: block git push without an explicit, single-use ticket.

The agent should use /ship or /commit, which (after user "y" confirmation)
run `tausik push-ok && git push`. `tausik push-ok` writes a 60-second TTL
ticket at `.tausik/.push_ticket.json`, bound to the current HEAD SHA and
branch. This hook consumes the ticket on a valid match and allows the push;
otherwise it blocks.

Why a ticket file instead of an env flag — Claude Code, Cursor and Qwen Code
all run PreToolUse hooks in the harness process, not the Bash subprocess.
Inline `VAR=val git push` env never reaches the hook, so the historical
TAUSIK_ALLOW_PUSH path was broken in every IDE. A file-based ticket works
identically across all of them.

Single-use + short TTL + bound-to-HEAD reduce the accidental-push risk
window. Determined agents can still call `tausik push-ok` themselves; the
ticket is a discipline rail, not a malicious-agent firewall — that role
belongs to `bash_firewall.py` (force-push to main) and IDE permissions.

Env knobs:
- TAUSIK_SKIP_PUSH_HOOK=1 — debug bypass (CI / local debugging only).
- TAUSIK_PUSH_TICKET_PATH — override ticket file location (tests).

Exit codes: 0 = allow, 2 = block.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

# Fallback matcher, used only when the command cannot be tokenized (unbalanced
# quotes). Token-based detection (`_command_invokes_git_push`) is the primary
# path — it does not false-positive on a 'git push' mention inside a quoted
# argument, which the substring regex did (blocking e.g. `tausik memory add
# "...git push..."`).
#
# Even as a fallback it must cover the shapes that used to sail past: `.exe` is
# explicit because `git.exe push` is idiomatic on Windows, and the option group
# covers any global git flag, not just `-c` (`git --no-pager push`, `git -C dir
# push`). Flags are matched before the subcommand only, so `git commit -m ...`
# cannot reach `push` through this pattern.
_GIT_PUSH_RE = re.compile(
    r"(?:^|[\s;&|()`])(?:[/\w.\\-]*[/\\])?git(?:\.exe)?"
    r"(?:\s+(?:-[cC]\s+\S+|--?[\w-]+(?:=\S+)?))*"
    r"\s+push\b",
    re.IGNORECASE,
)

# git global options that consume the FOLLOWING token as their value; they may
# sit between `git` and the `push` subcommand (`git -c k=v push`, `git -C repo
# push`). The `--opt=value` form is self-contained (one token) and needs no
# skip. Keeping this list lets a real push be detected even with global flags,
# while the token model still ignores quoted mentions.
_GIT_VALUE_OPTS = frozenset({"-c", "-C", "--git-dir", "--work-tree", "--namespace"})


def _tokens_invoke_git_push(tokens: list[str]) -> bool:
    """True iff the token stream invokes ``git ... push`` as a command.

    A tokenizer keeps a quoted argument as a SINGLE token, so a 'git push'
    mention inside a quoted string (``tausik task log "...git push..."``)
    collapses to one token and never matches — the substring-false-positive fix.
    That property is what the whole gate rests on, and it only holds while the
    tokenizer actually speaks the language the command was written in.
    """
    n = len(tokens)
    for i, tok in enumerate(tokens):
        if tok != "git" and os.path.basename(tok) not in ("git", "git.exe"):
            continue
        j = i + 1
        while j < n and tokens[j].startswith("-"):
            opt = tokens[j]
            j += 1
            if opt in _GIT_VALUE_OPTS:  # '--opt=value' form is self-contained
                j += 1
        if j < n and tokens[j] == "push":
            return True
    return False


def _command_invokes_git_push(command: str, tool_name: str = "") -> bool:
    """Detect a genuine ``git push`` invocation, ignoring quoted mentions.

    Primary: tokenize in the dialect `tool_name` speaks, and look for
    ``git ... push`` in command position. Fallback: if that dialect cannot read
    the line (unbalanced quotes), use the substring regex — conservative, since
    a real push must not slip through on a parse failure.

    The dialect follows the TOOL. An earlier version of this fix tried both
    tokenizers and blocked if either saw a push — sound against evasion, wrong
    in practice. `shlex(posix=True)` does not know a PowerShell here-string,
    breaks out of it at the first apostrophe, and then reads the `git push` in
    a commit message's PROSE as a command; of two judges, the one that cannot
    read the language wins every disagreement. It blocked an ordinary
    `git commit -m @'…'@` — and this gate is a discipline rail, not the
    malicious-agent firewall (that is `bash_firewall`, which catches force-push
    on both channels independently). A false block here costs more than a miss.

    Residual, stated rather than assumed: a push inside a WRAPPER payload
    (`powershell -Command 'git push'`) is one quoted token to its dialect and
    is not seen here. `bash_firewall` does descend into wrapper payloads, so a
    force-push that way is still caught; an ordinary push that way is not
    ticketed. Named in `docs/*/enforcement-coverage.md` — silence about it is
    what produced the task this gate was changed for.
    """
    from shell_channel import tokenize  # noqa: PLC0415

    tokens = tokenize(tool_name, command)
    if tokens is None:
        return bool(_GIT_PUSH_RE.search(command))
    return _tokens_invoke_git_push(tokens)


TICKET_FILENAME = ".push_ticket.json"
SCHEMA_VERSION = 1


def _find_tausik_dir(start: Path | None = None) -> Path | None:
    """Walk up from `start` (default CWD) looking for a .tausik directory."""
    cur = (start or Path.cwd()).resolve()
    for parent in (cur, *cur.parents):
        candidate = parent / ".tausik"
        if candidate.is_dir():
            return candidate
    return None


def _as_local_path(raw: str) -> Path:
    """Interpret a shell path the way the shell that ran it would.

    Git Bash spells drives as `/d/repo`; Python on Windows reads that as
    `\\d\\repo` on the current drive and finds nothing — the gate then fell
    back to the session directory and refused a legitimate push. Only the
    `/<letter>/…` shape is rewritten, and only on Windows, where it cannot
    collide with a real absolute path.
    """
    if os.name == "nt" and len(raw) > 2 and raw[0] == "/" and raw[2] == "/" and raw[1].isalpha():
        return Path(f"{raw[1]}:/{raw[3:]}")
    return Path(raw)


def _command_target_dir(command: str) -> Path | None:
    """Directory the push actually runs in, when the command says so.

    Work often happens in a repository next to the session directory —
    `cd ../lib && git push`, or `git -C ../lib push`. The hook runs with the
    session's CWD, so reading HEAD there compares the ticket against the
    wrong repository (or none at all, when the session directory isn't a
    repo). Both explicit forms are cheap to read; anything else falls back
    to CWD, which is what a bare `git push` means anyway.
    """
    # posix=False keeps backslashes intact: on Windows a posix split turns
    # `cd C:\repo` into `cd C:repo` and the directory is never found.
    try:
        tokens = [t.strip("\"'") for t in shlex.split(command, posix=False)]
    except ValueError:
        return None
    for i, tok in enumerate(tokens):
        if tok == "cd" and i + 1 < len(tokens):
            return _as_local_path(tokens[i + 1])
        if tok != "git" and os.path.basename(tok) not in ("git", "git.exe"):
            continue
        j = i + 1
        while j < len(tokens) and tokens[j].startswith("-"):
            if tokens[j] == "-C" and j + 1 < len(tokens):
                return _as_local_path(tokens[j + 1])
            j += 2 if tokens[j] in _GIT_VALUE_OPTS else 1
    return None


def _repo_root(start: Path | None = None) -> Path | None:
    out = _git(["rev-parse", "--show-toplevel"], cwd=start)
    if not out:
        return None
    try:
        return Path(out).resolve()
    except OSError:
        return None


def _ticket_path(target_dir: Path | None = None) -> Path | None:
    override = os.environ.get("TAUSIK_PUSH_TICKET_PATH")
    if override:
        return Path(override)
    # Prefer the .tausik belonging to the repository being pushed — `push-ok`
    # writes the ticket next to the repo it ran in — then the session's own.
    candidates = [d for d in (_find_tausik_dir(target_dir), _find_tausik_dir()) if d is not None]
    for tdir in candidates:
        if (tdir / TICKET_FILENAME).exists():
            return tdir / TICKET_FILENAME
    if candidates:
        return candidates[0] / TICKET_FILENAME
    # Nothing exists yet: still name where a ticket would be looked for, so
    # the refusal tells the caller which directory to run `push-ok` in.
    home = _repo_root(target_dir) or target_dir or Path.cwd()
    return home / ".tausik" / TICKET_FILENAME


def _git(args: list[str], cwd: Path | None = None) -> str | None:
    try:
        out = subprocess.check_output(
            ["git"] + args,
            stderr=subprocess.DEVNULL,
            # stdin=DEVNULL was MISSING here — the one genuinely-unguarded git call
            # (git-exec-single-wrapper), safe until now only because this hook's
            # stdin is consumed earlier. This hook runs as an isolated subprocess
            # with only hooks/ on sys.path (shared utils are duplicated into hooks/,
            # not imported from scripts/), so it keeps its own guarded call rather
            # than importing scripts/git_exec — the same standalone reasoning as
            # bootstrap. See v14b-defect-mcp-task-done-stdin-hang.
            stdin=subprocess.DEVNULL,
            timeout=3,
            cwd=str(cwd) if cwd else None,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError):
        return None
    return out.decode("utf-8", "replace").strip()


def _git_head_sha(cwd: Path | None = None) -> str | None:
    return _git(["rev-parse", "HEAD"], cwd=cwd)


def _consume_ticket(target_dir: Path | None = None) -> tuple[bool, str]:
    """Return (allow, reason). On allow, the ticket file has been deleted.

    `target_dir` is the directory the push runs in (see `_command_target_dir`);
    HEAD and the ticket's repo are both read there, so a session opened
    outside the repository — or in a directory that is no repository at all —
    still validates against the repo actually being pushed.
    """
    path = _ticket_path(target_dir)
    if path is None:
        return False, (
            "no .tausik directory found near the session or the repository "
            "being pushed — run `tausik init` first"
        )
    if not path.exists():
        return False, (
            f"no push ticket at {path} — run `tausik push-ok` first to "
            "authorize (run it inside the repository you are pushing)"
        )
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        return False, f"cannot read push ticket: {e}"
    try:
        ticket = json.loads(raw)
    except json.JSONDecodeError:
        return False, "push ticket is malformed JSON — re-run `tausik push-ok`"
    if not isinstance(ticket, dict):
        return False, "push ticket has unexpected shape"
    if ticket.get("schema_version") != SCHEMA_VERSION:
        return (
            False,
            f"push ticket schema_version != {SCHEMA_VERSION} — re-run `tausik push-ok`",
        )
    expires_str = ticket.get("expires_at", "")
    try:
        expires = datetime.fromisoformat(expires_str)
    except (TypeError, ValueError):
        return False, "push ticket expires_at is not a valid ISO datetime"
    now = datetime.now(timezone.utc)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if now >= expires:
        try:
            path.unlink()
        except OSError:
            pass
        return False, "push ticket expired — re-run `tausik push-ok`"
    # A ticket names the repository it was issued for (tickets written before
    # this field existed simply don't constrain it). Without the check, a
    # ticket taken for one repo would authorize a push from another.
    target_root = _repo_root(target_dir)
    ticket_root = ticket.get("repo_root") or ""
    if ticket_root and target_root is not None:
        try:
            same = Path(ticket_root).resolve() == target_root
        except OSError:
            same = False
        if not same:
            return False, (
                f"push ticket was issued for {ticket_root}, but this push runs "
                f"in {target_root} — run `tausik push-ok` in the repository "
                "you are pushing"
            )
    head = _git_head_sha(target_dir)
    ticket_sha = ticket.get("commit_sha", "")
    if head and ticket_sha and ticket_sha != head:
        return False, (
            f"push ticket SHA mismatch (HEAD {head[:8]}, ticket {ticket_sha[:8]}) "
            "— re-run `tausik push-ok` after committing"
        )
    try:
        path.unlink()
    except OSError as e:
        return False, f"cannot consume push ticket: {e}"
    return True, "ok"


def main() -> int:
    # hook-stderr-encoding-locale-dependent: this hook's messages contain
    # non-ASCII, and their readability must not depend on how it was
    # launched. Local import: hooks/ is sys.path[0] only when run as a script.
    from _common import emit_supervision_bypass, force_utf8_io

    force_utf8_io()

    if os.environ.get("TAUSIK_SKIP_PUSH_HOOK") == "1":
        emit_supervision_bypass(
            os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()), "skip_push_hook", "git_push_gate"
        )
        return 0

    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return 0

    command = data.get("tool_input", {}).get("command", "")
    if not command:
        return 0
    if not _command_invokes_git_push(command, str(data.get("tool_name") or "")):
        return 0

    allow, reason = _consume_ticket(_command_target_dir(command))
    if allow:
        return 0

    print(
        "BLOCKED: git push requires a TAUSIK push ticket.\n"
        f"Reason: {reason}\n"
        "Use /ship (review + gates + push) or /commit (commit + push). "
        "Both skills run `tausik push-ok && git push` after your 'y' "
        "confirmation. The ticket is single-use, expires in 60 seconds, "
        "and is bound to the current commit SHA.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
