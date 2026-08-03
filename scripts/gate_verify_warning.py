"""What a verify result must say out loud, beyond `passed`.

Split out of `service_gates` (filesize gate) and kept as a pure function: the
wording is the whole point of task `remediation-advice-does-not-remediate`, so
it deserves to be testable without a DB connection behind it.

Two silent traps live here, both of which read as a plain green:

  * verify PASSED with every gate skipped — nothing ran, and `task done` will
    later find no cached row and block with a confusing message.
  * verify hit the cache on a row recorded with ``scope='manual'`` — an operator
    asserted that green; no gate produced it. `record_run` writes a row on
    ``has_real_pass OR manual``, so the cache cannot tell the two apart on its
    own, and reading one back yields `passed=True, gates=[]` either way.
"""

from __future__ import annotations

from typing import Any


def build_verify_warning(
    *,
    passed: bool,
    status: str | None,
    has_real_pass: bool,
    manual_asserted: bool,
    gates_configured: bool,
    cache_row: dict[str, Any] | None,
) -> str | None:
    """Return the caveat this result needs, or None when the green is honest."""
    if status == "hit" and (cache_row or {}).get("scope") == "manual":
        row = cache_row or {}
        return (
            "CACHE HIT ON AN ASSERTED RUN — this green comes from "
            f"verification_run #{row.get('id')} (ran_at={row.get('ran_at')}), which "
            "was recorded with scope='manual': no gate actually passed there, an "
            "operator asserted it. `task done` will accept it. Re-run with real "
            "gates if you need actual verification."
        )
    if passed and not has_real_pass and status != "hit" and gates_configured:
        if manual_asserted:
            # Honoured, not a mistake: an asserted green was requested and
            # written, so telling the caller to "add tests" would be noise.
            return (
                "RECORDED AS MANUAL (asserted) — no real gate pass (skipped/no tests), "
                "but scope='manual' recorded it. `task done` Verify-First will see it."
            )
        return (
            "NOT CACHED — verify PASSED but no gate produced a real pass (skipped/no "
            "tests mapped). `task done` won't see it. Use scope='manual' or add tests."
        )
    return None
