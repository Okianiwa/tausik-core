"""Does a checker's own config-declared scope intersect this file slice?

A gate whose command carries no `{files}` placeholder resolves its own scope
from a config file (mypy -> `[tool.mypy] files/exclude` in pyproject.toml). On
the commit trigger the gates judge a temp tree holding ONLY staged content, so
a commit touching nothing inside that scope leaves the checker with no sources
— and it exits with a USAGE error, which reads as a failed gate. That blocks a
commit nothing was ever checked in.

The verdict is computed from the CONFIG, before the run. Matching the checker's
error text was tried (cd9db84) and failed: mypy emits at least three wordings
depending on whether the configured directory exists in the slice at all —
measured on mypy 2.3.0:

    slice holds scripts/hooks/ only  -> "There are no .py[i] files in directory 'scripts'"
    slice holds no scripts/ at all   -> "mypy: error: Cannot read file 'scripts': No such file or directory"

the second slipped past the marker list in session #35 and blocked a
requirements.txt-only commit. A signature on another tool's prose is the same
brittleness class this project has caught before; the config is ours to read.

Unknown tool, missing config, unreadable config -> `None` ("no opinion"), and
the caller runs the gate as before. Never skip a checker we failed to reason
about.
"""

from __future__ import annotations

import os
import re
import tomllib
from typing import Callable

_PY_SUFFIXES = (".py", ".pyi")

# Launchers that put the real tool name one or two tokens in: `python -m mypy`.
_PY_LAUNCHERS = {"python", "python3", "py", "pypy", "pypy3"}


def _posix(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _under(path: str, entry: str) -> bool:
    """Is `path` the config entry itself, or something beneath it?"""
    entry = _posix(entry).rstrip("/")
    return path == entry or path.startswith(entry + "/")


def _ancestors(path: str) -> list[str]:
    """`a/b/c.py` -> ['a/', 'a/b/', 'a/b/c.py'] — every node mypy walks through.

    mypy applies `exclude` while it walks, so a pattern naming a DIRECTORY
    (`scripts/hooks/`) prunes the whole subtree. Testing the file path alone
    would still match that example by luck (the pattern is a substring), but
    not an anchored one like `^scripts/hooks/$`.
    """
    parts = path.split("/")
    out = ["/".join(parts[:i]) + "/" for i in range(1, len(parts))]
    out.append(path)
    return out


def _read_tool_table(cwd: str, tool: str) -> dict | None:
    """`[tool.<name>]` from pyproject.toml in `cwd`, or None if not readable."""
    path = os.path.join(cwd, "pyproject.toml")
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    table = data.get("tool", {}).get(tool)
    return table if isinstance(table, dict) else None


def mypy_slice_in_scope(files: list[str], cwd: str) -> bool | None:
    """True if any file in `files` is one mypy would type-check here.

    Mirrors mypy's own resolution order: `files` entries select the roots,
    `exclude` regexes prune during the walk (unanchored `re.search`, per mypy's
    documented semantics).
    """
    table = _read_tool_table(cwd, "mypy")
    if table is None:
        return None
    roots = table.get("files")
    if isinstance(roots, str):
        roots = [roots]
    if not isinstance(roots, list) or not roots:
        # No `files=` — scope comes from the command line or from packages=/
        # modules=, neither of which this resolver models. No opinion.
        return None

    raw_exclude = table.get("exclude") or []
    if isinstance(raw_exclude, str):
        raw_exclude = [raw_exclude]
    try:
        excludes = [re.compile(p) for p in raw_exclude if isinstance(p, str)]
    except re.error:
        return None

    roots = [r for r in roots if isinstance(r, str)]
    for raw in files:
        path = _posix(raw)
        if not path.endswith(_PY_SUFFIXES):
            continue
        if not any(_under(path, r) for r in roots):
            continue
        nodes = _ancestors(path)
        if any(rx.search(node) for rx in excludes for node in nodes):
            continue
        return True
    return False


# Keyed by the tool's own executable name, not by gate name: a project may
# rename the gate, but `mypy` reading `[tool.mypy]` is a property of the tool.
_RESOLVERS: dict[str, Callable[[list[str], str], bool | None]] = {
    "mypy": mypy_slice_in_scope,
}


def _tool_name(cmd: str) -> str:
    """Executable a command invokes, seeing through `python -m <tool>`."""
    tokens = cmd.split()
    if not tokens:
        return ""
    head = os.path.basename(tokens[0]).lower()
    if head.endswith(".exe"):
        head = head[:-4]
    if head in _PY_LAUNCHERS and len(tokens) >= 3 and tokens[1] == "-m":
        return os.path.basename(tokens[2]).lower()
    return head


def slice_intersects_config_scope(
    gate: dict, files: list[str], cwd: str | None = None
) -> bool | None:
    """Would this gate's checker find anything to look at in `files`?

    Returns None when the question does not apply — the command names its own
    inputs via `{files}`, the tool has no resolver, or its config could not be
    read. Callers treat None as "run the gate".
    """
    cmd = gate.get("command") or ""
    if not cmd or "{files}" in cmd or "{test_files_for_files}" in cmd:
        return None
    resolver = _RESOLVERS.get(gate.get("config_scope") or _tool_name(cmd))
    if resolver is None:
        return None
    return resolver(files, cwd or os.getcwd())
