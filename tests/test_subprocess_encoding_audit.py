"""Regression guard for the locale-decode defect (commit b395a07).

`subprocess.run(..., text=True)` without an explicit codec decodes through
the host locale. On cp1251/cp866 Windows that mangles UTF-8 child output and
— worse — raises inside the reader thread, so the call returns rc=0 with
stdout=None. The b395a07 sweep fixed `scripts/hooks/` only; the rest of the
repo kept the defect until this task. This module keeps it from returning.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from audit_subprocess_encoding import scan_path, scan_source  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNED_DIRS = ("scripts", "tests", "bootstrap")

# Non-ASCII on purpose: this is what the locale decoder mangles.
NON_ASCII_PAYLOAD = "2× hard cap — proof"


class TestRepoStaysClean:
    def test_no_unprotected_subprocess_calls(self) -> None:
        findings = []
        for name in SCANNED_DIRS:
            findings.extend(scan_path(REPO_ROOT / name))
        assert findings == [], "unprotected subprocess text-mode call(s):\n" + "\n".join(
            str(f) for f in findings
        )


class TestDetectorIsNotBlind:
    """A detector that never fires would make the guard above vacuous."""

    def test_flags_text_true_without_encoding(self) -> None:
        src = 'subprocess.run(["x"], capture_output=True, text=True)'
        assert len(scan_source(src, Path("synthetic.py"))) == 1

    def test_flags_universal_newlines(self) -> None:
        src = 'subprocess.Popen(["x"], universal_newlines=True)'
        assert len(scan_source(src, Path("synthetic.py"))) == 1

    @pytest.mark.parametrize(
        "src",
        [
            'subprocess.run(["x"], text=True, encoding="utf-8")',
            'subprocess.run(["x"], text=True, errors="replace", encoding="utf-8")',
            'subprocess.run(["x"], text=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})',
            'subprocess.run(["x"], text=True, **kwargs)',
            'subprocess.run(["x"], capture_output=True)',
        ],
    )
    def test_accepts_protected_forms(self, src: str) -> None:
        assert scan_source(src, Path("synthetic.py")) == []


class TestExplicitCodecActuallyMatters:
    """Mutation anchor for `encoding=` itself.

    The cost-budget hook now prints pure ASCII, so dropping `encoding=` from
    that test no longer breaks it (verified by mutation during this task).
    This test keeps a live case where the codec is load-bearing: drop the
    `encoding=` below and it fails on any non-UTF-8 locale host.
    """

    def test_non_ascii_child_output_roundtrips(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", f"print({NON_ASCII_PAYLOAD!r})"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={"PYTHONIOENCODING": "utf-8"},
            timeout=30,
            check=False,
        )
        assert result.stdout is not None, "reader thread died — the b395a07 silent failure"
        assert NON_ASCII_PAYLOAD in result.stdout


class TestCostBudgetHookStaysAscii:
    """Guards the product-side half of the fix (decision: ASCII in hook output)."""

    def test_hook_message_builder_is_ascii(self) -> None:
        hook = REPO_ROOT / "scripts" / "hooks" / "task_cost_budget_check.py"
        source = hook.read_text(encoding="utf-8")
        start = source.index("cap_label = ")
        end = source.index('return f"[TAUSIK cost-budget')
        emitted = source[start:end]
        offenders = sorted({c for c in emitted if ord(c) > 127})
        assert offenders == [], f"non-ASCII in user-facing hook output: {offenders}"
