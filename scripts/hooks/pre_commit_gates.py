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

    stdin defaults to DEVNULL so a child can never block waiting for input,
    but `subprocess.run` rejects `input` and `stdin` together — a caller
    feeding git a path list needs the pipe, so the default yields to it.
    """
    if "input" not in kwargs:
        kwargs.setdefault("stdin", subprocess.DEVNULL)
    return subprocess.run(  # noqa: S603
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
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

    Paths go over stdin (`--stdin -z`), not argv: this is the second place the
    32767-char Windows command line overflows on a whole-tree commit, and it
    fails before the gates ever run. NUL-separated to match the `-z` form
    `staged_files` already reads — git tolerates a missing trailing NUL here.
    """
    prefix = str(dest).replace("\\", "/").rstrip("/") + "/"
    proc = _run(
        ["git", "checkout-index", "--stdin", "-z", "--prefix=" + prefix],
        cwd=str(root),
        input="\0".join(files),
        timeout=_GIT_TIMEOUT,
    )
    if proc.returncode != 0 and proc.stderr.strip():
        print(f"[pre-commit] git checkout-index: {proc.stderr.strip()}", file=sys.stderr)

    for name in _CONFIG_FILES:
        src = root / name
        if src.is_file():
            shutil.copy2(src, dest / name)

    return [f for f in files if (dest / f).is_file()]


def write_manifest(path: Path, files: list[str]) -> None:
    """Write the file list NUL-TERMINATED, matching the `-z` form git gave us.

    Terminated, not separated, so the reader can tell the format from the bytes:
    a one-entry separated manifest holds no NUL at all and would be
    indistinguishable from a line-based one.

    `newline=""` disables translation. Without it, text mode rewrites any `\\n`
    on the way out and universal-newline mode rewrites any lone `\\r` on the way
    back in, so a round trip is not guaranteed to return what it was given.

    Note this is belt-and-braces, not a live hazard: git's `verify_path()`
    rejects a path containing raw `\\r` or `\\n` outright (checked against git
    2.49 — `git update-index --cacheinfo` answers "error: Invalid path"), and
    Win32 will not create such a filename either. `staged_files` therefore
    cannot hand us one. The NUL form is kept because it costs nothing and
    matches the convention `staged_files` already reads with.
    """
    blob = "".join(f"{f}\0" for f in files)
    path.write_text(blob, encoding="utf-8", newline="")


def run_gates(root: Path, workdir: Path, files: list[str], manifest: Path) -> tuple[int, str]:
    """Run the commit gates over `files`, passed by manifest rather than argv.

    Windows caps a command line at 32767 characters. Measured on this repo:
    979 repo-relative paths fit, 980 do not — and overflow does not surface as
    "too many files", it surfaces as `FileNotFoundError: [WinError 206]`, whose
    class name points at a missing file that does not exist. A whole-tree commit
    (mass reformat, licence-header sweep, initial import) reaches that ceiling.

    Batching was rejected (decision #56): several gates judge the file SET, not
    each file — `tdd_order` would fail a batch holding sources whose tests
    landed in another, and stack inference reads the union of extensions. That
    changes what the gates mean, not just how they are invoked.
    """
    gate_runner = root / "scripts" / "gate_runner.py"
    if not gate_runner.is_file():
        return 0, "[pre-commit] gate_runner.py not found — skipping (legacy checkout)."

    env = dict(os.environ)
    # Gates run from the temp tree, so point config resolution back at the repo.
    env["TAUSIK_DIR"] = str(root / ".tausik")
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        # Inside the try: writing the manifest touches a just-created scratch
        # dir, and that write fails for the same Windows reasons the teardown
        # does — an antivirus mid-scan, a full disk. Left outside, it reached
        # the developer as a raw traceback.
        write_manifest(manifest, files)
        proc = _run(
            [sys.executable, str(gate_runner), "commit", "--files-from", str(manifest)],
            cwd=str(workdir),
            env=env,
            timeout=_GATE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        # Not an OSError, and _GATE_TIMEOUT is reachable on a slow run rather
        # than exotic — so it needs its own arm and its own wording. Reusing the
        # launch-failure text here would name the wrong cause.
        return 1, (
            f"[pre-commit] gate run exceeded {_GATE_TIMEOUT}s and was killed "
            f"({len(files)} staged file(s)).\n"
            "[pre-commit] Re-run, or bypass: git commit --no-verify "
            "(also skips the Mojang scan)."
        )
    except OSError as exc:
        # The manifest keeps the file list out of argv, so this is no longer the
        # overflow path — but if the command line ever gets long again, say so
        # instead of handing over a raw WinError.
        return 1, (
            f"[pre-commit] could not run gate_runner: {exc}\n"
            f"[pre-commit] {len(files)} staged file(s); the list was passed via "
            f"{manifest}, so the command line itself is short.\n"
            "[pre-commit] Bypass: git commit --no-verify (also skips the Mojang scan)."
        )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def discard_tree(path: str) -> None:
    """Remove the staged-content tree, downgrading any failure to a warning.

    Cleanup must never decide the commit. On Windows `rmtree` can fail long
    after the gates have returned their verdict — an open handle from a gate
    subprocess, an antivirus scan mid-flight, a read-only file. Letting that
    propagate turns a *passing* run into a rejected commit for a reason that
    has nothing to do with the staged code, and hands the developer a
    `tempfile` traceback instead of a gate report.

    A leaked temp directory is worth a warning. It is not worth a block.
    """
    try:
        shutil.rmtree(path)
    except Exception as exc:  # noqa: BLE001 — see above: never outrank the verdict
        print(
            f"[pre-commit] could not remove temp tree {path}: {exc}\n"
            "[pre-commit] gates were unaffected; delete it by hand if it persists.",
            file=sys.stderr,
        )


def judge(root: Path, scratch: Path, files: list[str]) -> int:
    """Decide the commit against staged content materialized under `scratch`.

    Split out of `main` so the verdict is fully computed — and reported —
    before the scratch area is discarded, rather than after.

    The materialized tree sits in `scratch/tree` so the file manifest can live
    beside it without appearing to be a repo file the gates should judge.
    """
    dest = scratch / "tree"
    dest.mkdir(parents=True, exist_ok=True)

    present = materialize_index(root, files, dest)
    if not present:
        return 0

    # Licence check first: it is the only failure here that cannot be
    # undone by a follow-up commit.
    violations = mojang_artifact_scan.scan(present, dest)
    if violations:
        print(mojang_artifact_scan.format_report(violations), file=sys.stderr)
        return 1

    code, output = run_gates(root, dest, present, scratch / "staged-files.txt")
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

    # mkdtemp + try/finally rather than `with TemporaryDirectory(...)`: the
    # context manager cleans up on the way out of the block, which put the
    # teardown *ahead* of reading the return code. `finally` runs after the
    # return value is evaluated, and `discard_tree` cannot raise, so the
    # verdict survives a failed cleanup either way.
    tmp = tempfile.mkdtemp(prefix="tausik-precommit-")
    try:
        return judge(root, Path(tmp), files)
    finally:
        discard_tree(tmp)


if __name__ == "__main__":
    sys.exit(main())
