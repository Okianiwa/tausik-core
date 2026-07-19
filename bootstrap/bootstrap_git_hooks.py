"""Install TAUSIK git hooks into `.git/hooks/`.

Separate from `bootstrap_hooks.py`, which builds the harness (Claude Code /
Cursor / Qwen) hook dict for settings.json. Those fire on tool calls inside
one IDE; these fire on git itself, so they also cover GUI clients, plain
`git commit` in a terminal, and any other agent — the coverage the harness
layer cannot reach.

Installed as a thin shim rather than a copy: the shim resolves the real
implementation under `scripts/hooks/` at run time, so editing the source
takes effect without re-running bootstrap, and the hook cannot drift from
the repo it guards.

Windows note: git runs hooks through its bundled sh, so the shim is POSIX
sh regardless of host OS. Python is located at run time, preferring the
project venv.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

HOOK_SHIM = """#!/bin/sh
# TAUSIK {hook_name} hook (installed by bootstrap; edit the Python source instead).
# Bypass: git commit --no-verify   |   disable: TAUSIK_SKIP_COMMIT_GATES=1
set -e
ROOT="$(git rev-parse --show-toplevel)"
IMPL="$ROOT/{impl_rel}"
[ -f "$IMPL" ] || IMPL="$ROOT/.claude/hooks/{impl_name}"
[ -f "$IMPL" ] || exit 0

PY="$ROOT/.tausik/venv/Scripts/python.exe"
[ -f "$PY" ] || PY="$ROOT/.tausik/venv/bin/python"
[ -f "$PY" ] || PY="python3"
command -v "$PY" >/dev/null 2>&1 || PY="python"

exec "$PY" "$IMPL" "$@"
"""

# hook filename -> implementation module under scripts/hooks/
GIT_HOOKS = {
    "pre-commit": "pre_commit_gates.py",
}


def _git_hooks_dir(project_dir: str | Path) -> Path | None:
    """Resolve `.git/hooks`, handling worktrees where `.git` is a file."""
    git_path = Path(project_dir) / ".git"
    if git_path.is_dir():
        return git_path / "hooks"
    if git_path.is_file():
        # Worktree/submodule: ".git" holds "gitdir: <path>".
        try:
            content = git_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if content.startswith("gitdir:"):
            target = Path(content.split(":", 1)[1].strip())
            if not target.is_absolute():
                target = Path(project_dir) / target
            return target / "hooks"
    return None


def install_git_hooks(project_dir: str | Path) -> list[str]:
    """Install TAUSIK git hooks. Returns names installed.

    No-op (not an error) outside a git repo — bootstrap must stay usable on
    a plain directory.
    """
    hooks_dir = _git_hooks_dir(project_dir)
    if hooks_dir is None:
        return []
    hooks_dir.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    for hook_name, impl_name in GIT_HOOKS.items():
        target = hooks_dir / hook_name
        content = HOOK_SHIM.format(
            hook_name=hook_name,
            impl_rel=f"scripts/hooks/{impl_name}",
            impl_name=impl_name,
        )
        if target.exists():
            try:
                existing = target.read_text(encoding="utf-8")
            except OSError:
                existing = ""
            # Never clobber a hand-written hook; ours is idempotent to rewrite.
            if existing and "TAUSIK" not in existing:
                print(
                    f"  WARNING: {target} exists and is not a TAUSIK hook — left untouched. "
                    f"Merge it manually to enable commit gates."
                )
                continue
            if existing == content:
                installed.append(hook_name)
                continue

        target.write_text(content, encoding="utf-8", newline="\n")
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        installed.append(hook_name)

    return installed


def uninstall_git_hooks(project_dir: str | Path) -> list[str]:
    """Remove TAUSIK-installed git hooks, leaving foreign hooks alone."""
    hooks_dir = _git_hooks_dir(project_dir)
    if hooks_dir is None:
        return []

    removed: list[str] = []
    for hook_name in GIT_HOOKS:
        target = hooks_dir / hook_name
        if not target.is_file():
            continue
        try:
            if "TAUSIK" not in target.read_text(encoding="utf-8"):
                continue
            os.remove(target)
            removed.append(hook_name)
        except OSError:
            continue
    return removed
