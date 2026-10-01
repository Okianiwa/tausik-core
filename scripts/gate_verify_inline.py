"""Verify gates run INSIDE `task done` — the path QG-2 takes when no cached
green can count: `task_done.auto_verify=true`, and a security-sensitive scope,
whose greens the cache refuses by design and which `tausik verify` will not
mint a handle for. Without the second caller such a task could never close.
"""

from __future__ import annotations

from typing import Any

from gate_block import _block, extract_files_from_gate_output


def run_verify_inline(
    svc: Any,
    report: dict[str, Any],
    slug: str,
    relevant_files: list[str],
    *,
    cause: str,
    crash_remediation: str,
) -> None:
    from service_verification import run_gates_with_cache

    try:
        passed, results, _status = run_gates_with_cache(
            svc.be._conn,
            slug,
            relevant_files,
            scope=report.get("scope") or "standard",
            append_notes_fn=svc.be.task_append_notes,
            trigger="verify",
            # These gates are running INSIDE a task_done. The run is
            # recorded under trigger=verify so it shares the cache bucket,
            # but it must not mint a presentable handle: that would let a
            # close certify itself, and a close that blocks after this point
            # would leave a valid hour-long handle behind for a task that
            # never closed (v2-verify-receipt-as-argument).
            allow_handle=False,
        )
    except Exception as e:  # noqa: BLE001 — best-effort: telemetry/degradation, non-fatal to the main flow
        _block(report, "verify-first", f"{cause} run crashed: {e}", crash_remediation)
        return
    if not passed:
        report["passed"] = False
        blocking = [r for r in results if not r.get("passed") and r.get("severity") == "block"]
        report["blocking_failures"].extend(
            {
                "gate": r.get("name"),
                "files": extract_files_from_gate_output(r.get("output", "")),
                "output": r.get("output", ""),
                "remediation": f"Fix gate issues and rerun task_done. ({cause} caused inline run.)",
            }
            for r in blocking
        )
