"""Command-gate executor — substitutes placeholders + runs subprocess.

Extracted from gate_runner.py for filesize compliance
(v14b-filesize-debt-paydown). Public surface:

    _SCOPED_SKIP_SENTINEL — return marker for skipped scoped runs
    _NOTHING_TO_CHECK_SENTINEL — marker for "the checker's config selects
        nothing in this slice", decided before the run (gate_config_scope)
    run_command_gate(gate, files, trigger=None) -> (passed, output)

Behaviour is identical to the previous in-place implementation; gate_runner
re-exports both names for backwards compatibility with existing callers
(no import changes required elsewhere).
"""

from __future__ import annotations

import os
import shlex
import subprocess

from gate_config_scope import slice_intersects_config_scope
from gate_test_resolver import resolve_test_files_for_relevant


_SCOPED_SKIP_SENTINEL = "__TAUSIK_SCOPED_SKIP__"

# A checker invoked without {files} resolves its own scope from config (mypy:
# files=["scripts"], exclude=["scripts/hooks/"]). On the commit trigger it judges a
# temp tree holding ONLY staged content, so a commit touching nothing inside that
# scope leaves the checker with no sources and it exits with a USAGE error — a
# block on a commit it never type-checked. Empty input is not a failure: nothing
# to check is a skip. Which case this is comes from reading the checker's config
# (gate_config_scope), not from its error prose. Kept separate from the scoped
# sentinel so the reason names the true cause.
_NOTHING_TO_CHECK_SENTINEL = "__TAUSIK_NOTHING_TO_CHECK__"

# The slice is the whole world only on `commit` — that trigger materializes
# staged content into a temp tree. `task-done`, `verify` and `review` run against
# the real worktree, where an empty intersection with relevant_files means the
# caller scoped the run, NOT that the checker has nothing to look at. Skipping
# there would silently retire the gate.
_SLICE_IS_THE_TREE = ("commit",)


def _apply_trigger_args(cmd: str, gate: dict, trigger: str | None) -> str:
    """Append the extra arguments a gate declares for THIS trigger, if any.

    A checker can be sound on one trigger and structurally unsound on another,
    and that difference belongs to the trigger rather than to the command
    string — which is shared by every trigger the gate lists. mypy is the
    measured case: on `commit` it judges a tree of staged files only, so every
    unstaged neighbour degrades to Any and `no-any-return` fires on code that
    is correct (see default_gates.py for the numbers).

    Ignores a non-str value instead of failing the run: a malformed override
    is a config problem, reported by `stack_schema` / `validate_custom_gate`,
    and turning it into a gate error here would block a commit for a reason
    that has nothing to do with the staged code.
    """
    if not trigger:
        return cmd
    extra = (gate.get("trigger_args") or {}).get(trigger)
    if not isinstance(extra, str) or not extra.strip():
        return cmd
    return f"{cmd} {extra.strip()}"


def run_command_gate(gate: dict, files: list[str], trigger: str | None = None) -> tuple[bool, str]:
    """Run a command-based gate. Substitutes {files} / {test_files_for_files}.

    Appends `trigger_args[trigger]` when the gate declares one — see
    `_apply_trigger_args`.

    Special return: (True, _SCOPED_SKIP_SENTINEL) when {test_files_for_files}
    is in cmd and no test files map from a non-empty relevant_files. The
    caller (run_gates) translates this into a skipped_result entry so the
    UI shows SKIP, not PASS, and we don't run an irrelevant full suite.

    Also (True, _NOTHING_TO_CHECK_SENTINEL) when `trigger` is one where `files`
    IS the tree being judged and the checker's own config selects nothing in it.
    Absent `trigger`, that skip never fires — a caller that does not say which
    trigger it is gets the gate run, not waived.
    """
    cmd = gate.get("command", "")
    if not cmd:
        return True, "No command configured."

    # file_extensions gates relevance, NOT just path substitution: a gate that
    # reads its scope from a config file (mypy -> [tool.mypy] in pyproject) has
    # no {files} placeholder, and keying the filter on the placeholder let it
    # run on doc-only commits — at severity=block that rejects a .md commit
    # over an unrelated .py error.
    file_exts_raw = gate.get("file_extensions") or []
    if file_exts_raw:
        allowed = {(e if e.startswith(".") else "." + e).lower() for e in file_exts_raw}
        files = [f for f in files if os.path.splitext(f)[1].lower() in allowed]
        if not files:
            return True, ("No files matching " + ", ".join(sorted(allowed)) + " — gate skipped.")

    # Decided from the checker's config, BEFORE the run — see gate_config_scope
    # for why reading its error text does not work. `None` = no opinion (unknown
    # tool, unreadable config): run the gate.
    if trigger in _SLICE_IS_THE_TREE:
        in_scope = slice_intersects_config_scope(gate, files)
        if in_scope is False:
            return True, _NOTHING_TO_CHECK_SENTINEL

    cmd = _apply_trigger_args(cmd, gate, trigger)

    if "{test_files_for_files}" in cmd:
        test_files = resolve_test_files_for_relevant(files)
        # Scoped-only semantics:
        #   - relevant_files non-empty + no test mapping → SKIP (scoped run for
        #     a module without test_<basename>.py — running the full suite for
        #     an unrelated module defeats the scoping promise).
        #   - relevant_files empty → SKIP (was: fall back to full suite).
        #     MCP task_done has a 10s budget; the suite always exceeds it and
        #     burns budget for zero verification value. Forces callers to pass
        #     relevant_files to opt in to actual verification.
        if not test_files:
            return True, _SCOPED_SKIP_SENTINEL
        test_files_str = " ".join(shlex.quote(t) for t in test_files)
        cmd = cmd.replace("{test_files_for_files}", test_files_str)

    files_str = " ".join(shlex.quote(f) for f in files) if files else "."
    cmd = cmd.replace("{files}", files_str)
    # v14b-pytest-fast-lane: TAUSIK_VERIFY_FULL=1 reverts the default fast lane
    # (pyproject.toml addopts="-m 'not slow'") and runs the full battery —
    # subprocess/integration/e2e/stress tests included. Only applies to pytest.
    if os.environ.get("TAUSIK_VERIFY_FULL") and cmd.lstrip().startswith("pytest"):
        head, _, tail = cmd.partition(" ")
        cmd = (
            f"{head} --override-ini=addopts= {tail}" if tail else f"{head} --override-ini=addopts="
        )
    # Detect shell operators -- need shell=True for pipes and redirects
    needs_shell = any(op in cmd for op in ("|", "&&", ">>", "2>&1"))
    timeout = gate.get("timeout", 120)
    try:
        if needs_shell:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
        else:
            result = subprocess.run(
                shlex.split(cmd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
        output = (result.stdout + result.stderr).strip()
        if result.returncode == 0:
            return True, output or "Passed."
        return False, output or f"Failed with exit code {result.returncode}."
    except subprocess.TimeoutExpired:
        return False, f"Gate timed out ({timeout}s)."
    except Exception as e:
        return False, f"Gate error: {e}"
