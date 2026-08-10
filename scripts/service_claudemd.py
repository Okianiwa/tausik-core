"""Single builder for the CLAUDE.md DYNAMIC section — shared by CLI and MCP.

mcp-update-claudemd-drops-memory-tail: the MCP handler kept its own copy of
this logic without build_compact_memory_tail, so every MCP call silently
erased the memory tail the CLI had written. Content assembly lives here now;
both entry points delegate.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

MARKER_START_PREFIX = "<!-- DYNAMIC:START"
MARKER_END = "<!-- DYNAMIC:END -->"


def resolve_branch(project_dir: str | None = None) -> str:
    """Current git branch: subprocess first (worktree-safe), .git/HEAD fallback."""
    cwd = project_dir or os.getcwd()
    # stdin=DEVNULL: avoids inherit of MCP JSON-RPC pipe (v14b-defect-mcp-task-done-stdin-hang).
    try:
        r = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
            stdin=subprocess.DEVNULL,
            cwd=cwd,
        )
        if branch := r.stdout.strip():
            return branch
    except Exception:
        pass
    try:
        with open(os.path.join(cwd, ".git", "HEAD"), encoding="utf-8") as f:
            ref = f.read().strip()
        return ref.replace("ref: refs/heads/", "") if ref.startswith("ref:") else ref[:8]
    except Exception:
        return "unknown"


def resolve_version() -> str:
    try:
        from tausik_version import __version__

        return __version__
    except ImportError:
        return "unknown"


def build_dynamic_content(
    svc: Any, branch: str, version: str, warnings: list[str] | None = None
) -> str:
    """Current State block + memory tail. The only place this content is assembled.

    A failed tail build degrades to no-tail output but reports into `warnings`
    so callers can voice the degradation instead of shipping a silent green.
    """
    tasks = svc.task_list()
    session = svc.session_current()

    active = [t for t in tasks if t["status"] == "active"]
    blocked = [t for t in tasks if t["status"] == "blocked"]
    done_count = sum(1 for t in tasks if t["status"] == "done")

    session_info = f"#{session['id']} (active)" if session else "none"
    lines = [
        "## Current State",
        f"Session: {session_info} | Branch: {branch} | Version: {version}",
        f"Tasks: {done_count}/{len(tasks)} done, {len(active)} active, {len(blocked)} blocked",
    ]
    if active:
        lines.append(f"Active: {', '.join(t['slug'] for t in active)}")
    if blocked:
        lines.append(f"Blocked: {', '.join(t['slug'] for t in blocked)}")

    if (be := getattr(svc, "be", None)) is not None:
        from service_knowledge_aggregates import build_compact_memory_tail

        if memory_tail := build_compact_memory_tail(be, errors=warnings):
            lines.append("")
            lines.extend(memory_tail)

    return "\n".join(lines)


def replace_dynamic_section(original: str, dynamic_content: str) -> str | None:
    """Swap the DYNAMIC block; None when the start marker is missing."""
    start_idx = original.find(MARKER_START_PREFIX)
    if start_idx == -1:
        return None
    nl = original.find("\n", start_idx)
    start_line_end = nl if nl != -1 else len(original)
    before = original[:start_line_end]
    if MARKER_END in original:
        after = original[original.index(MARKER_END) :]
        return f"{before}\n{dynamic_content}\n{after}"
    return f"{before}\n{dynamic_content}\n{MARKER_END}\n"
