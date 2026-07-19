"""Commit gate: staged `scripts/` must match the deployed copy under `.<ide>/scripts/`.

The code lives in two copies. `scripts/` is the source — what git tracks, what
pytest imports, what the commit gates judge. The runtime (MCP server, hooks)
executes `.claude/scripts/`, a deploy produced by `bootstrap.py` and kept out of
git. Edit the source without re-bootstrapping and every signal goes green while
the change never reaches the process that runs it: that is how the encoding fix
of session #29 survived to session #30 without once taking effect (task
`fix-bootstrap-drift-deployed-scripts-stale`, memory #107).

`tausik doctor` already spots this, but it is advisory and hand-run — the same
shape of failure `fix-no-precommit-hook-gates-decorative` closed: a rule with no
mechanism. This gate is the mechanism.

Blocks rather than auto-deploying (decision #55): a commit must not mutate state
outside the index; auto-deploy would publish *staged* bytes that can match
neither index nor worktree; and a running MCP server holds the old modules in
memory regardless, so a silent redeploy would restore exactly the false-green it
is meant to kill.

Two scoping rules keep it from blocking anything it does not understand:

  * **Only inside the TAUSIK source checkout.** "`scripts/` deploys to
    `.claude/scripts/`" is true of this repository, not of the projects TAUSIK
    bootstraps. A consumer with its own `scripts/etl_job.py` would otherwise be
    permanently un-committable — and told to run a `bootstrap/bootstrap.py` that
    does not exist in its checkout, leaving `--no-verify` as the only exit.
  * **Index drift and deploy drift get different remedies.** The gate judges the
    index; `bootstrap.py` deploys the worktree. When those disagree, no amount of
    re-bootstrapping moves the deploy toward the index, so naming bootstrap as
    the fix would loop forever. `git add` is the fix for that case, and the
    report says so.

Escape hatch: `TAUSIK_SKIP_COMMIT_GATES=1`, or `git commit --no-verify`.
"""

from __future__ import annotations

import os
from typing import NamedTuple

# Mirrors bootstrap.py::_IDE_DIRS. Duplicated rather than imported because
# bootstrap/ is not on the path when gates run from the staged temp tree.
_IDE_DEPLOY_DIRS = {
    "claude": ".claude",
    "cursor": ".cursor",
    "windsurf": ".windsurf",
    "codex": ".codex",
    "qwen": ".qwen",
}
_DEFAULT_DEPLOY_DIR = ".claude"

_SOURCE_PREFIX = "scripts/"

# Files bootstrap copies but that carry no runtime behaviour worth a block.
_IGNORED_SEGMENTS = ("__pycache__",)
_IGNORED_SUFFIXES = (".pyc", ".pyo")

#: `kind` values — which tool actually clears the violation.
DEPLOY_DRIFT = "deploy"  # bootstrap.py fixes it
INDEX_DRIFT = "index"  # git add fixes it
BOTH_DRIFT = "both"  # index, worktree and deploy all disagree — needs both


class Violation(NamedTuple):
    path: str
    reason: str
    kind: str
    deploy: str


def _normalize(path: str) -> str:
    """Canonicalize for prefix matching: forward slashes, no leading './'."""
    norm = os.path.normpath(path).replace("\\", "/")
    return norm[2:] if norm.startswith("./") else norm


def _read(path: str) -> bytes | None:
    """File bytes with CRLF folded to LF, or None if unreadable.

    Normalised because `git checkout-index` honours `core.autocrlf` while
    bootstrap copies bytes verbatim — a newline-only difference is not drift.
    """
    try:
        with open(path, "rb") as fh:
            return fh.read().replace(b"\r\n", b"\n")
    except OSError:
        return None


def project_root() -> str | None:
    """Repo root — the parent of the resolved `.tausik/` directory, or None.

    Under the pre-commit hook this gate runs from a temp tree holding staged
    content, so cwd is not the checkout; `TAUSIK_DIR` (exported by
    `scripts/hooks/pre_commit_gates.py`) points resolution back at the real one.
    Run by hand from the repo, the upward search finds it instead.

    Returns None when resolution fails. The caller blocks on that rather than
    guessing: the whole job here is deciding *whether* the two copies agree, and
    a gate that answers "they agree" because it could not find one of them is
    worse than no gate — it launders a failure into a green.
    """
    try:
        from project_config import find_tausik_dir  # noqa: PLC0415

        return os.path.dirname(os.path.abspath(find_tausik_dir()))
    except Exception:  # noqa: BLE001 — reported as a block, not a crash
        return None


def is_tausik_source(root: str) -> bool:
    """True only for a checkout of TAUSIK itself.

    Positive markers, not the absence of negatives: a consumer project can have
    `scripts/` and `.claude/`, so only the presence of the generator that
    creates the source→deploy relationship proves the invariant applies.
    """
    return os.path.isfile(os.path.join(root, "bootstrap", "bootstrap.py")) and os.path.isdir(
        os.path.join(root, "harness")
    )


def deploy_dirs(root: str) -> list[str] | None:
    """IDE deploy directories to compare against, or None if config is unreadable.

    `bootstrap.py --ide all` writes `ide="all"` and deploys to every IDE dir, so
    a single-name lookup would check `.claude` and silently ignore drift in
    `.cursor` and `.qwen`.

    Fails closed for the same reason `project_root` does. Guessing `.claude` when
    the configured IDE could not be read turns a *real* deploy elsewhere into
    "no deploy exists" and passes the commit — a silent bypass of a blocking
    gate, which is the exact defect this file was written to prevent.
    """
    try:
        from project_config import load_config  # noqa: PLC0415

        bootstrap_cfg = (load_config() or {}).get("bootstrap") or {}
        if not isinstance(bootstrap_cfg, dict):
            return None
        ide = bootstrap_cfg.get("ide")
    except Exception:  # noqa: BLE001 — reported as a block, not a crash
        return None

    if ide == "all":
        candidates = list(_IDE_DEPLOY_DIRS.values())
    else:
        candidates = [_IDE_DEPLOY_DIRS.get(ide or "", _DEFAULT_DEPLOY_DIR)]
    return [d for d in candidates if os.path.isdir(os.path.join(root, d, "scripts"))]


def _repo_relative(entry: str, root: str | None) -> str | None:
    """Repo-relative form of `entry`, or None if it lies outside the tree.

    Absolute paths reach this gate through `relevant_files` on non-commit
    callers. Left unconverted they fail the `scripts/` prefix test and the file
    silently drops out of scope — an unconditional green.
    """
    norm = _normalize(entry)
    if not os.path.isabs(norm):
        return norm
    for base in (os.getcwd(), root):
        if not base:
            continue
        try:
            rel = os.path.relpath(norm, base).replace("\\", "/")
        except ValueError:  # different drive on Windows
            continue
        if not rel.startswith(".."):
            return rel
    return None


def in_scope(files: list[str], root: str | None = None) -> list[tuple[str, str]]:
    """[(path as given, repo-relative path)] for the files this gate judges.

    Everything git tracks under `scripts/` counts, not just `.py`: bootstrap
    copies the whole tree, so a stale `scripts/README.md` is the same class of
    divergence. Compiled artefacts are excluded — they are regenerated, never
    authored, and `.gitignore` keeps them out of the index anyway.
    """
    out: list[tuple[str, str]] = []
    for entry in files:
        rel = _repo_relative(entry, root)
        if rel is None or not rel.startswith(_SOURCE_PREFIX):
            continue
        # Segment match, not substring: copy_dir excludes the __pycache__
        # *directory*, so a tracked file merely containing the word is ours.
        if any(seg in rel.split("/") for seg in _IGNORED_SEGMENTS):
            continue
        if rel.endswith(_IGNORED_SUFFIXES):
            continue
        out.append((entry, rel))
    return out


def _classify(rel: str, staged: bytes, live: bytes | None, worktree_root: str) -> tuple[str, str]:
    """(reason, kind) for a staged file whose deploy does not match it.

    `bootstrap.py` copies the *worktree*. So when the deploy already equals the
    worktree, the disagreement is between index and worktree — re-bootstrapping
    would copy the same bytes again and the block would survive it. Saying
    "run bootstrap" there sends the reader into a loop they cannot exit except
    with `--no-verify`, which is how a discipline rail gets abandoned.

    When all three disagree, one step is genuinely not enough: bootstrap moves
    the deploy to the worktree, and the index is still behind. Naming only the
    first half would clear one block and raise another — a promise of
    "…then commit" that the next commit breaks.
    """
    worktree = _read(os.path.join(worktree_root, *rel.split("/")))

    if live is None:
        return "deployed copy could not be read", DEPLOY_DRIFT
    if worktree is None:
        return "absent from the worktree, so bootstrap cannot refresh it", INDEX_DRIFT
    if worktree == live:
        return "staged content differs from the worktree that the deploy mirrors", INDEX_DRIFT
    if worktree == staged:
        return "deployed copy is stale", DEPLOY_DRIFT
    return "index, worktree and deploy all disagree", BOTH_DRIFT


def scan(
    scoped: list[tuple[str, str]],
    deployed_scripts: str,
    deploy_dir: str,
    worktree_root: str,
) -> tuple[list[Violation], int]:
    """Return (violations, files actually compared).

    The count is not `len(scoped)`: a path that never materialized is skipped,
    and reporting it as verified would make "checked and matched" and "never
    looked" produce the same sentence.
    """
    violations: list[Violation] = []
    compared = 0

    for entry, rel in scoped:
        if not os.path.isfile(entry):
            continue

        staged = _read(entry)
        if staged is None:
            violations.append(
                Violation(rel, "staged content could not be read", DEPLOY_DRIFT, deploy_dir)
            )
            continue

        compared += 1
        target = os.path.join(deployed_scripts, *rel[len(_SOURCE_PREFIX) :].split("/"))

        if not os.path.isfile(target):
            if not os.path.isfile(os.path.join(worktree_root, *rel.split("/"))):
                violations.append(
                    Violation(
                        rel,
                        "staged but absent from the worktree, so bootstrap cannot deploy it",
                        INDEX_DRIFT,
                        deploy_dir,
                    )
                )
            else:
                violations.append(Violation(rel, "not deployed at all", DEPLOY_DRIFT, deploy_dir))
            continue

        live = _read(target)
        if live is not None and live == staged:
            continue

        reason, kind = _classify(rel, staged, live, worktree_root)
        violations.append(Violation(rel, reason, kind, deploy_dir))

    return violations, compared


def format_report(violations: list[Violation], deploys: list[str]) -> str:
    """Remedy first — `gate_runner.format_results` prints only the first 5 lines.

    Bury the fix under the file list and the one line the reader needs is the
    one that gets truncated away; the count goes in the header for the same
    reason, so a truncated report still says how much it is hiding.
    """
    kinds = {v.kind for v in violations}
    needs_add = bool(kinds & {INDEX_DRIFT, BOTH_DRIFT})
    needs_bootstrap = bool(kinds & {DEPLOY_DRIFT, BOTH_DRIFT})

    if needs_add and needs_bootstrap:
        head = "Run `python bootstrap/bootstrap.py` AND `git add` the files below, then commit."
    elif needs_add:
        head = "Index is behind the worktree — `git add` the files below, then commit."
    else:
        head = "Deploy is stale — run `python bootstrap/bootstrap.py`, then commit."

    where = ", ".join(f"{d}/scripts/" for d in deploys)
    # Distinct paths, not raw violation count: with several deploy dirs one
    # stale file yields one violation per dir, and a header that counts those
    # tells the reader to go find files that do not exist.
    total = len({v.path for v in violations})
    lines = [
        head,
        f"{total} staged source(s) differ from {where}, what the runtime executes:",
    ]
    for v in violations:
        tag = f" [{v.deploy}]" if len(deploys) > 1 else ""
        lines.append(f"  {v.path} — {v.reason} ({v.kind}){tag}")
    lines += [
        "",
        "A running MCP server also caches the old modules — restart the IDE session.",
    ]
    return "\n".join(lines)


def run_bootstrap_drift_gate(gate: dict, files: list[str]) -> tuple[bool, str]:
    """Gate entry point. Signature mirrors gate_runner's other built-ins.

    `gate_applies_to` only filters on `stacks`, so this runs on every commit;
    scoping is ours to do. Each early return names the reason it did not
    compare, because "nothing to compare" and "everything matches" are
    different facts and a single cheerful message for both is how the defect
    this gate exists to catch got missed in the first place.
    """
    root = project_root()
    if root is None:
        return False, (
            "Cannot resolve the project root, so source and deploy cannot be compared.\n"
            "Set TAUSIK_DIR, or bypass with TAUSIK_SKIP_COMMIT_GATES=1 if this is not "
            "a TAUSIK checkout."
        )

    if not is_tausik_source(root):
        return (
            True,
            "Not a TAUSIK source checkout (no bootstrap/ + harness/) — gate not applicable.",
        )

    scoped = in_scope(files, root)
    if not scoped:
        return True, "No staged scripts/ sources — nothing to compare."

    dirs = deploy_dirs(root)
    if dirs is None:
        return False, (
            "Cannot read bootstrap.ide from .tausik/config.json, so the deploy directory "
            "is unknown and nothing can be compared.\n"
            "Fix the config, or bypass with TAUSIK_SKIP_COMMIT_GATES=1."
        )
    if not dirs:
        return True, "No IDE deploy of scripts/ exists yet — nothing to compare against."

    all_violations: list[Violation] = []
    compared = 0
    for deploy_dir in dirs:
        found, n = scan(scoped, os.path.join(root, deploy_dir, "scripts"), deploy_dir, root)
        all_violations.extend(found)
        compared = max(compared, n)

    if all_violations:
        return False, format_report(all_violations, dirs)
    if compared == 0:
        return True, "No staged scripts/ source could be read — nothing was compared."
    return True, f"{compared} staged source(s) match {', '.join(dirs)}."
