"""Scope guard: a gate judges only files that belong to THIS project.

A task may legitimately list files from another repository — this project keeps
the task ledger while the code lives elsewhere (portfolio work). Those files are
governed by the OTHER project's rules, so applying this project's gates to them
produces a verdict about a rule that does not hold there. Two measured cases: a
479-line test file from a repo whose own contract exempts tests blocked
`task done` here on `filesize: max 400`; and a 706-line script of the ops
repository did the same a week later, closing nothing that this project owns.

Narrowing the scope silently would be the worse bug of the two, so the note
built here is rendered next to the gate result even when the gate passes.
"""

from __future__ import annotations

import os
import sys

_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)


def project_root() -> str | None:
    """The directory holding this project's `.tausik/`, or None when unknown.

    None rather than a guess: `find_tausik_dir` falls back to `cwd/.tausik`
    whether or not that directory exists, and a root invented from the process's
    cwd is exactly the wrong answer for a guard that decides which files stop
    being checked. Unknown is its own answer and keeps everything in scope.
    """
    try:
        from project_config import find_tausik_dir  # noqa: PLC0415

        tausik = find_tausik_dir()
    except Exception:  # noqa: BLE001 — an unresolvable root must not raise into a gate run
        return None
    if not tausik or not os.path.isdir(tausik):
        return None
    return os.path.dirname(os.path.abspath(tausik))


def split_by_project_root(files: list[str]) -> tuple[list[str], list[str]]:
    """Split into (inside, outside) by this project's root.

    RELATIVE PATHS ARE ALWAYS INSIDE, and that is the load-bearing rule rather
    than a shortcut. The pre-commit hook runs gates with cwd set to a temp tree
    holding staged content and points config resolution back at the checkout via
    `TAUSIK_DIR`. Resolving a relative path against cwd there would place every
    staged file outside the root and drop the whole commit out of scope — gates
    would report green having checked nothing. Only absolute paths carry enough
    information to be judged foreign.

    An unresolvable root keeps every file in scope: a guard that cannot tell
    where it is must not be the reason a check stops running.
    """
    root = project_root()
    if not root:
        return list(files), []
    root_n = os.path.normcase(os.path.normpath(os.path.abspath(root)))
    inside: list[str] = []
    outside: list[str] = []
    for f in files:
        if not os.path.isabs(f):
            inside.append(f)
            continue
        cand = os.path.normcase(os.path.normpath(os.path.abspath(f)))
        # Prefix comparison, not os.path.commonpath: the latter raises on paths
        # from different drives, which is exactly the case this guard exists for.
        if cand == root_n or cand.startswith(root_n + os.sep):
            inside.append(f)
        else:
            outside.append(f)
    return inside, outside


def external_scope_note(outside: list[str], *, scope_emptied: bool) -> str:
    """One-line-plus-list note naming what this project did NOT check, and why.

    `scope_emptied` distinguishes "checked the rest" from "checked nothing",
    because the second reads as a clean pass unless it says otherwise.
    """
    listed = ", ".join(outside)
    if scope_emptied:
        return (
            f"NOT CHECKED HERE: all {len(outside)} file(s) live outside this project "
            f"and are governed by their own project's rules — {listed}. This gate "
            "verified NOTHING; it is not a passing check."
        )
    return (
        f"SCOPE: {len(outside)} file(s) outside this project were left to their own "
        f"project's rules and NOT checked here — {listed}."
    )
