"""Where the stack registry reads its two layers from.

Split out of `stack_registry.py` for the filesize gate, but the split also
isolates the thing that was wrong: these paths used to be derived from
`__file__`, which made the registry a property of *which copy of the code is
running* rather than of the project. Code executing out of `scripts/` saw 25
stacks while the same code out of `.claude/scripts/` saw 1, so one config.json
produced two behaviours — a gate that was a dead stub under the CLI ran for
real under the commit hook (task gates-registry-split-cli-vs-hook, decision
 #64).

Two questions, two answers:
  * "which stacks EXIST?"                 -> `catalog_stacks_dir()`
  * "which stacks does THIS PROJECT run?" -> `active_builtin_dir()`
"""

from __future__ import annotations

import os

# Mirrors bootstrap.py::_IDE_DIRS, ordered by preference. Duplicated rather
# than imported for the same reason gate_bootstrap_drift duplicates it:
# bootstrap/ is not importable when this runs from a deploy or a staged tree.
_IDE_DEPLOY_DIRS = (".claude", ".cursor", ".windsurf", ".codex", ".qwen")


def catalog_stacks_dir() -> str:
    """Every stack TAUSIK ships — the library's `stacks/`, not the deploy's.

    Resolved from the project, then from this file, because `__file__` alone
    splits the catalog the same way it split the gate registry: measured, code
    running out of `.claude/scripts/` saw 1 stack and out of `scripts/` saw 25,
    so `--stack java` validated from a terminal and was rejected over MCP.

    Order: the framework checkout's own `stacks/`, then a project-local
    `.tausik-lib/stacks/`, then the copy beside this file. The last is the
    deploy when running from one — a truncated catalog, but the only thing
    left when the library lives outside the project (hub install).
    """
    root = project_root()
    if root is not None:
        for rel in ("stacks", os.path.join(".tausik-lib", "stacks")):
            candidate = os.path.join(root, rel)
            if os.path.isdir(candidate):
                return candidate
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(scripts_dir), "stacks")


def project_root() -> str | None:
    """Project root: parent of `.tausik/`, or None when it cannot be found.

    Resolved WITHOUT importing `project_config.find_tausik_dir`, even though
    that function answers exactly this question. Measured: `project_config`
    imports `DEFAULT_GATES` at line 213 while defining `find_tausik_dir` at
    line 290, so importing it from here closes a cycle through
    `default_gates._build_stack_scoped_gates`. That failure is not loud — both
    that function and `project_types._load_default_stacks` swallow it in a
    broad `except`, leaving the registry silently collapsed to the 6 universal
    gates. Duplicating ~10 lines beats a fallback that lies.

    `TAUSIK_DIR` comes first because the pre-commit hook runs gates from a temp
    tree holding staged content: cwd is not the checkout there, and only the
    env var points back at the real project.
    """
    env_dir = os.environ.get("TAUSIK_DIR")
    if env_dir:
        return os.path.dirname(os.path.abspath(env_dir))

    cur = os.path.abspath(os.getcwd())
    while True:
        if os.path.isdir(os.path.join(cur, ".tausik")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def user_stacks_dir() -> str:
    """`.tausik/stacks/` of the current project — the user override layer."""
    return os.path.join(project_root() or os.getcwd(), ".tausik", "stacks")


def active_builtin_dir() -> str:
    """Stacks the project actually deploys, falling back to the full catalog.

    The fallback covers early bootstrap only — before `bootstrap.py` has
    written any deploy dir there is nothing project-specific to read, and a
    registry that raised there would break the very tool that creates it.
    """
    root = project_root()
    if root is not None:
        for ide_dir in _IDE_DEPLOY_DIRS:
            candidate = os.path.join(root, ide_dir, "stacks")
            if os.path.isdir(candidate):
                return candidate
    return catalog_stacks_dir()
