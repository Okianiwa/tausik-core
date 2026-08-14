"""tausik push-ok — write a single-use push ticket consumed by git_push_gate.

Writes `.tausik/.push_ticket.json` (schema_version=1) with the current HEAD
SHA, branch, and an expires_at timestamp (default now+60s). The hook
consumes the ticket on a valid match; missing, expired, or HEAD-mismatched
tickets keep blocking. Use after the user has explicitly confirmed a push
in /commit or /ship — never preemptively.

CLI dispatch:
    cmd_push_ok(svc_unused, args) -> None  # exits with sys.exit(1) on error
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import git_exec

TICKET_FILENAME = ".push_ticket.json"
SCHEMA_VERSION = 1
DEFAULT_TTL_SECONDS = 60


def _find_tausik_dir(start: Path | None = None) -> Path | None:
    cur = (start or Path.cwd()).resolve()
    for parent in (cur, *cur.parents):
        candidate = parent / ".tausik"
        if candidate.is_dir():
            return candidate
    return None


def _git(args: list[str]) -> str | None:
    try:
        # git_exec closes stdin (defense-in-depth: never read an inherited MCP stdin pipe).
        result = git_exec.run(args, timeout=3)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return str(result.stdout.strip())


def write_push_ticket(
    tausik_dir: Path,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    *,
    commit_sha: str | None = None,
    branch: str | None = None,
    repo_root: str | None = None,
) -> Path:
    """Write a single-use ticket atomically. Returns the ticket path."""
    if commit_sha is None:
        commit_sha = _git(["rev-parse", "HEAD"]) or ""
    if branch is None:
        branch = _git(["rev-parse", "--abbrev-ref", "HEAD"]) or ""
    if branch == "HEAD":
        branch = ""  # detached
    if repo_root is None:
        repo_root = _git(["rev-parse", "--show-toplevel"]) or ""
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=ttl_seconds)
    ticket = {
        "schema_version": SCHEMA_VERSION,
        "commit_sha": commit_sha,
        "branch": branch,
        # Which repository this ticket authorizes. The gate refuses it for any
        # other repo, so a ticket taken for the library can't open a push in
        # the project that happens to be the session's directory.
        "repo_root": repo_root,
        "created_at": now.isoformat(),
        "expires_at": expires.isoformat(),
    }
    tausik_dir.mkdir(parents=True, exist_ok=True)
    path = tausik_dir / TICKET_FILENAME
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(ticket, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def cmd_push_ok(_svc_unused: Any, args: Any) -> None:
    """CLI handler for `tausik push-ok`. Exits 1 on error, 0 on success."""
    raw_ttl = getattr(args, "ttl", None)
    ttl = DEFAULT_TTL_SECONDS if raw_ttl is None else raw_ttl
    if ttl <= 0:
        print("error: --ttl must be a positive number of seconds", file=sys.stderr)
        sys.exit(1)
    sha = _git(["rev-parse", "HEAD"])
    if not sha:
        print(
            "error: cannot determine HEAD commit — run `tausik push-ok` inside "
            "the repository you are pushing (this directory is not one, or has "
            "no commits yet)",
            file=sys.stderr,
        )
        sys.exit(1)
    repo_root = _git(["rev-parse", "--show-toplevel"]) or ""
    # The gate looks for the ticket beside the repository being pushed, then
    # in the session's own .tausik. A repository that carries no TAUSIK of its
    # own — the ops repo, a plain checkout — would otherwise have nowhere to
    # put one, which is how a legitimate push became unauthorizable.
    tausik_dir = _find_tausik_dir()
    if tausik_dir is None:
        if not repo_root:
            print(
                "error: no .tausik directory found — run `tausik init` first",
                file=sys.stderr,
            )
            sys.exit(1)
        tausik_dir = Path(repo_root) / ".tausik"
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"]) or ""
    path = write_push_ticket(
        tausik_dir, ttl_seconds=ttl, commit_sha=sha, branch=branch, repo_root=repo_root
    )
    short = sha[:8]
    branch_str = branch if branch and branch != "HEAD" else "(detached)"
    print(f"push ticket written: {path} (commit {short}, branch {branch_str}, ttl {ttl}s)")
