#!/usr/bin/env python3
"""git pre-commit hook: run TAUSIK commit gates against the staged tree.

Why this exists: `gate_runner.py` has supported the `commit` trigger all
along, but nothing ever invoked it from git — `.git/hooks/` held only
`*.sample`, and the legacy `scripts/hooks/pre-commit` ran mypy, never the
gates. So the CLAUDE.md rule "no commit without gates" described a mechanism
that did not exist; it held on agent discipline alone.

Judged on the INDEX, not the worktree. A gate that reads the working copy
answers a different question than "is what I am committing acceptable" —
`git add` a clean version, then keep editing, and a worktree-based gate
either misses a violation or invents one. Staged content is extracted with
`git checkout-index` into a temp tree that mirrors the repo layout, because
both gates are path-sensitive:
  - filesize `exempt_files` matches canonical repo-relative paths
    (async-platform/mc/tick-profile.md and friends)
  - ruff `per-file-ignores` in pyproject.toml likewise (bootstrap/*, tests/*)
Flattening or absolutising those paths would silently drop every exemption
and bury the developer in false positives — the fastest way to get a hook
deleted.

Escape hatch: `git commit --no-verify`, by design. This is a discipline
rail, not a firewall.

Exit codes: 0 = allow commit, 1 = blocking gate failure.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mojang_artifact_scan  # noqa: E402

# Copied into the temp tree so path-scoped tool config still resolves.
_CONFIG_FILES = ("pyproject.toml", "ruff.toml", ".ruff.toml", "setup.cfg")

_GIT_TIMEOUT = 30
_GATE_TIMEOUT = 300


def _run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    """Run a command in UTF-8 text mode.

    Explicit codec: on a cp1251/cp866 Windows host the default locale decode
    mangles output and can kill the reader thread while still returning
    rc=0 (commit b395a07 / task fix-cost-budget-hook-encoding-windows).
    """
    return subprocess.run(  # noqa: S603
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        check=False,
        **kwargs,  # type: ignore[arg-type]
    )


def repo_root() -> Path | None:
    proc = _run(["git", "rev-parse", "--show-toplevel"], timeout=_GIT_TIMEOUT)
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.strip())


def staged_files(root: Path) -> list[str]:
    """Repo-relative paths staged for commit, excluding deletions."""
    proc = _run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        cwd=str(root),
        timeout=_GIT_TIMEOUT,
    )
    if proc.returncode != 0:
        return []
    return [p for p in proc.stdout.split("\0") if p]


def materialize_index(root: Path, files: list[str], dest: Path) -> list[str]:
    """Extract staged content into `dest`, preserving repo-relative layout.

    Returns the subset that actually landed — `checkout-index` skips paths it
    cannot resolve, and gating a file we failed to read would be a lie.
    """
    prefix = str(dest).replace("\\", "/").rstrip("/") + "/"
    proc = _run(
        ["git", "checkout-index", "--prefix=" + prefix, "--"] + files,
        cwd=str(root),
        timeout=_GIT_TIMEOUT,
    )
    if proc.returncode != 0 and proc.stderr.strip():
        print(f"[pre-commit] git checkout-index: {proc.stderr.strip()}", file=sys.stderr)

    for name in _CONFIG_FILES:
        src = root / name
        if src.is_file():
            shutil.copy2(src, dest / name)

    return [f for f in files if (dest / f).is_file()]


def run_gates(root: Path, workdir: Path, files: list[str]) -> tuple[int, str]:
    gate_runner = root / "scripts" / "gate_runner.py"
    if not gate_runner.is_file():
        return 0, "[pre-commit] gate_runner.py not found — skipping (legacy checkout)."

    env = dict(os.environ)
    # Gates run from the temp tree, so point config resolution back at the repo.
    env["TAUSIK_DIR"] = str(root / ".tausik")
    env["PYTHONIOENCODING"] = "utf-8"

    proc = _run(
        [sys.executable, str(gate_runner), "commit", "--files"] + files,
        cwd=str(workdir),
        env=env,
        timeout=_GATE_TIMEOUT,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    if os.environ.get("TAUSIK_SKIP_COMMIT_GATES") == "1":
        return 0

    root = repo_root()
    if root is None:
        print("[pre-commit] not a git repository — skipping gates.", file=sys.stderr)
        return 0

    files = staged_files(root)
    if not files:
        return 0

    with tempfile.TemporaryDirectory(prefix="tausik-precommit-") as tmp:
        dest = Path(tmp)
        present = materialize_index(root, files, dest)
        if not present:
            return 0

        # Licence check first: it is the only failure here that cannot be
        # undone by a follow-up commit.
        violations = mojang_artifact_scan.scan(present, dest)
        if violations:
            print(mojang_artifact_scan.format_report(violations), file=sys.stderr)
            return 1

        code, output = run_gates(root, dest, present)

    if code == 0:
        return 0

    print(output.rstrip(), file=sys.stderr)
    print(
        "\nCOMMIT BLOCKED by TAUSIK gates (judged on staged content).\n"
        "Fix the blocking failures above, `git add` the fixes, and commit again.\n"
        "Emergency bypass: git commit --no-verify",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
