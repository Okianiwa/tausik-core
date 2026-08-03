"""TAUSIK gate runner -- execute quality gates for a given trigger.

Usage: python gate_runner.py <trigger> [--files file1 file2 ...]
Triggers: task-done, commit, review

Exit codes:
  0 -- all gates passed (or only warnings)
  1 -- at least one blocking gate failed
"""

from __future__ import annotations

import argparse
import os
import subprocess  # noqa: F401 — re-exported attr for backwards-compat monkeypatching (`gate_runner.subprocess.run`); the module is `subprocess` itself, so patching it here patches it globally for gate_command_runner too.
import sys
import time
from typing import Any, Callable

_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

from project_config import get_gates_for_trigger, load_config  # noqa: E402


from gate_bootstrap_drift import run_bootstrap_drift_gate  # noqa: E402
from gate_stack_dispatch import (  # noqa: E402,F401
    gate_applies_to,
    infer_stacks_from_files,
    skipped_result,
)


# Built-in (command=None) gate bodies live in gate_builtin_checks; re-exported
# here so existing `from gate_runner import run_filesize_gate` keeps working.
from gate_builtin_checks import (  # noqa: E402,F401
    _FILESIZE_EXEMPT_BASENAMES,
    _FILESIZE_EXEMPT_DIRS,
    _normalize_path,
    count_lines,
    run_filesize_gate,
    run_tdd_order_gate,
)
from gate_test_resolver import resolve_test_files_for_relevant  # noqa: F401, E402

# v14b-filesize-debt-paydown: run_command_gate + _SCOPED_SKIP_SENTINEL extracted
# to gate_command_runner.py (filesize compliance). Re-exported here so existing
# callers (e.g. tests/test_gates.py) continue importing them from gate_runner.
from gate_command_runner import (  # noqa: F401, E402
    _NOTHING_TO_CHECK_SENTINEL,
    _SCOPED_SKIP_SENTINEL,
    run_command_gate,
)


def run_gates(
    trigger: str,
    files: list[str] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[bool, list[dict]]:
    """Run all enabled gates for a trigger.

    Returns (all_passed, results) where all_passed means no blocking gate failed.
    Each result: {name, severity, passed, output}.
    """
    cfg = load_config()
    gates = get_gates_for_trigger(trigger, cfg)
    if not gates:
        return True, []

    results = []
    has_block_failure = False

    total = len(gates)
    # v1.4 r14-mcp-streaming-progress: emit a "run_start" event with the
    # max budget sum so MCP hosts (VS Code Claude Extension etc.) can show
    # an ETA before pytest blocks the channel for tens of seconds.
    if progress_callback:
        try:
            timeout_sum = 0
            for g in gates:
                t = g.get("timeout_seconds") or g.get("timeout") or 0
                try:
                    timeout_sum += int(t)
                except (TypeError, ValueError):
                    continue
            progress_callback(
                {
                    "event": "run_start",
                    "trigger": trigger,
                    "total": total,
                    "max_seconds": timeout_sum,
                    "gates": [g.get("name") for g in gates],
                }
            )
        except Exception:
            pass
    for idx, gate in enumerate(gates, start=1):
        name = gate["name"]
        severity = gate.get("severity", "warn")
        start_ms = time.monotonic()
        if progress_callback:
            progress_callback(
                {
                    "event": "gate_start",
                    "index": idx,
                    "total": total,
                    "name": name,
                    "severity": severity,
                }
            )

        if not gate_applies_to(gate, files or []):
            skipped = skipped_result(gate, files or [])
            results.append(skipped)
            if progress_callback:
                progress_callback(
                    {
                        "event": "gate_done",
                        "index": idx,
                        "total": total,
                        "name": name,
                        "severity": severity,
                        "passed": True,
                        "skipped": True,
                        "duration_ms": int((time.monotonic() - start_ms) * 1000),
                        "output": skipped.get("output", ""),
                    }
                )
            continue

        if name == "filesize":
            passed, output = run_filesize_gate(gate, files or [])
        elif name == "tdd_order":
            passed, output = run_tdd_order_gate(gate, files or [])
        elif name == "bootstrap_drift":
            passed, output = run_bootstrap_drift_gate(gate, files or [])
        else:
            passed, output = run_command_gate(gate, files or [], trigger=trigger)

        # Scoped-skip sentinel from run_command_gate: either relevant_files
        # were provided but no test files mapped, OR no relevant_files at
        # all (full-suite fallback removed in v1.3 — burns MCP 10s budget).
        if output in (_SCOPED_SKIP_SENTINEL, _NOTHING_TO_CHECK_SENTINEL):
            if output == _NOTHING_TO_CHECK_SENTINEL:
                skip_reason = (
                    f"NOT RUN: `{gate.get('command', name)}` selects its own inputs "
                    "from its config (mypy: [tool.mypy] files/exclude in "
                    "pyproject.toml), and no staged file falls inside that scope. "
                    "The checker was never invoked — nothing was verified here, so "
                    "there is nothing to block on. This is not a passing check."
                )
            else:
                skip_reason = (
                    "No test file maps to relevant_files via "
                    "tests/test_<basename>.py heuristic; gate skipped (scoped run)."
                    if files
                    else (
                        "No relevant_files passed; gate skipped. Pass relevant_files "
                        "for actual verification (e.g. --relevant-files src/foo.py)."
                    )
                )
            results.append(
                {
                    "name": name,
                    "severity": severity,
                    "passed": True,
                    "skipped": True,
                    "output": skip_reason,
                }
            )
            if progress_callback:
                progress_callback(
                    {
                        "event": "gate_done",
                        "index": idx,
                        "total": total,
                        "name": name,
                        "severity": severity,
                        "passed": True,
                        "skipped": True,
                        "duration_ms": int((time.monotonic() - start_ms) * 1000),
                        "output": skip_reason,
                    }
                )
            continue

        result = {
            "name": name,
            "severity": severity,
            "passed": passed,
            "output": output,
        }
        results.append(result)
        if progress_callback:
            progress_callback(
                {
                    "event": "gate_done",
                    "index": idx,
                    "total": total,
                    "name": name,
                    "severity": severity,
                    "passed": passed,
                    "skipped": False,
                    "duration_ms": int((time.monotonic() - start_ms) * 1000),
                    "output": output,
                }
            )

        if not passed and severity == "block":
            has_block_failure = True

    return not has_block_failure, results


def format_results(results: list[dict]) -> str:
    """Format gate results for display."""
    if not results:
        return "No gates configured for this trigger."
    lines = []
    for r in results:
        if r.get("skipped"):
            icon = "SKIP"
        elif r["passed"]:
            icon = "PASS"
        else:
            icon = "FAIL"
        sev = f" ({r['severity']})" if not r["passed"] else ""
        lines.append(f"  [{icon}] {r['name']}{sev}")
        # SKIP prints its reason too: a skipped gate verified NOTHING, and a bare
        # [SKIP] beside a [PASS] reads as "checked, fine". Naming why — which
        # config selected no inputs — is the difference between a waived gate the
        # reader can audit and one that quietly stopped mattering.
        if r["output"] and (not r["passed"] or r.get("skipped")):
            for line in r["output"].split("\n")[:5]:
                lines.append(f"         {line}")
    return "\n".join(lines)


def check_file_conflicts(tasks: list[dict]) -> list[tuple[str, str, list[str]]]:
    """Check if tasks have overlapping relevant_files.

    Args:
        tasks: list of dicts with 'slug' and 'relevant_files' (comma-separated string or None)

    Returns:
        List of (slug1, slug2, shared_files) tuples for conflicts.
    """
    file_map: dict[str, list[str]] = {}
    for task in tasks:
        slug = task.get("slug", "")
        files_str = task.get("relevant_files") or ""
        if not files_str:
            continue
        files = [f.strip() for f in files_str.split(",") if f.strip()]
        for f in files:
            file_map.setdefault(f, []).append(slug)

    conflicts = []
    seen = set()
    for _f, slugs in file_map.items():
        if len(slugs) > 1:
            for i, s1 in enumerate(slugs):
                for s2 in slugs[i + 1 :]:
                    pair = (min(s1, s2), max(s1, s2))
                    if pair not in seen:
                        seen.add(pair)
                        shared = [ff for ff, ss in file_map.items() if s1 in ss and s2 in ss]
                        conflicts.append((pair[0], pair[1], shared))
    return conflicts


def read_files_manifest(path: str) -> list[str]:
    """Read a file list written by the pre-commit hook.

    The hook writes NUL-TERMINATED entries, so the presence of a NUL identifies
    the format even for a single-entry list. Falls back to lines so a manifest
    written by hand also works.

    `newline=""` disables universal-newline translation, which would otherwise
    rewrite a lone `\\r` into `\\n` and hand the gates a path that was never
    staged.
    """
    with open(path, encoding="utf-8", newline="") as fh:
        blob = fh.read()
    parts = blob.split("\0") if "\0" in blob else blob.splitlines()
    return [p for p in parts if p]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run TAUSIK quality gates")
    parser.add_argument("trigger", choices=["task-done", "commit", "review"])
    parser.add_argument("--files", nargs="*", default=[])
    parser.add_argument(
        "--files-from",
        metavar="PATH",
        help=(
            "Read the file list from PATH instead of argv. Windows caps a command "
            "line at 32767 chars — ~979 repo-relative paths — and overflow surfaces "
            "as an opaque WinError 206. Combines with --files."
        ),
    )
    args = parser.parse_args()

    files = list(args.files)
    if args.files_from:
        files.extend(read_files_manifest(args.files_from))

    all_passed, results = run_gates(args.trigger, files)
    print(f"Gates for '{args.trigger}':")
    print(format_results(results))

    if not all_passed:
        print("\nBLOCKED: Fix blocking gate failures before proceeding.")
        sys.exit(1)
    elif any(not r["passed"] for r in results):
        print("\nWARNINGS: Non-blocking issues found. Consider fixing.")
    else:
        print("\nAll gates passed.")


if __name__ == "__main__":
    main()
