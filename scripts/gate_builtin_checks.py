"""Built-in gate checks — the ones with no shell command behind them.

`filesize` and `tdd_order` are dispatched by name in `gate_runner.run_gates`
rather than through `run_command_gate`, so their implementations have no
natural home in the command path. Extracted here to keep `gate_runner` under
the 400-line filesize budget, following the same split that produced
`gate_command_runner` and `gate_stack_dispatch`.

Re-exported from `gate_runner` so existing imports keep working.
"""

from __future__ import annotations

import os


def count_lines(filepath: str) -> int:
    """Count lines in a file."""
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


_FILESIZE_EXEMPT_DIRS = (
    "tests/",
    "harness/claude/mcp/",
    "harness/cursor/mcp/",
    "harness/qwen/mcp/",
    ".claude/mcp/",
    # Common exempt dirs for source materials, ADR markdowns, agent configs.
    "docs/content/",
    "docs/architecture/",
    "backend/configs/",
)

# Append-only files that grow unboundedly by design — exempt from line cap.
_FILESIZE_EXEMPT_BASENAMES = frozenset(
    {
        "CHANGELOG.md",
        "CHANGELOG.ru.md",
    }
)


def _normalize_path(p: str) -> str:
    """Canonicalize path for matching: forward slashes, strip leading './'."""
    n = os.path.normpath(p).replace("\\", "/")
    if n.startswith("./"):
        n = n[2:]
    return n


def run_filesize_gate(gate: dict, files: list[str]) -> tuple[bool, str]:
    """Check file sizes against max_lines threshold.

    Exempt: tests, MCP handlers (dispatchers, not creative logic).
    Per-file exempts via gate.exempt_files: entries with '/' match by exact
    path, bare names match by basename (covers a file anywhere in tree).
    """
    max_lines = gate.get("max_lines", 400)
    exempt_paths: set[str] = set()
    exempt_basenames: set[str] = set()
    for entry in gate.get("exempt_files") or []:
        norm = entry.replace("\\", "/")
        if "/" in norm:
            exempt_paths.add(_normalize_path(norm))
        else:
            exempt_basenames.add(norm)

    violations = []
    for f in files:
        if not os.path.isfile(f):
            continue
        normalized = f.replace("\\", "/")
        if any(d in normalized for d in _FILESIZE_EXEMPT_DIRS):
            continue
        canon = _normalize_path(f)
        basename = os.path.basename(canon)
        if canon in exempt_paths or basename in exempt_basenames:
            continue
        if basename in _FILESIZE_EXEMPT_BASENAMES:
            continue
        lines = count_lines(f)
        if lines > max_lines:
            violations.append(f"  {f}: {lines} lines (max {max_lines})")
    if violations:
        return False, "Files exceeding line limit:\n" + "\n".join(violations)
    return True, "All files within line limit."


def run_tdd_order_gate(gate: dict, files: list[str]) -> tuple[bool, str]:
    """Check that test files are present among changed files.

    TDD enforcement: if source files were changed, test files should also be changed.
    Skips if only non-code files were modified.
    """
    code_exts = {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".php",
    }
    test_patterns = (
        "test_",
        "_test.",
        ".test.",
        ".spec.",
        "Test.",  # Java/Kotlin: FooTest.java, FooTest.kt
        "Tests.",  # Java/Kotlin: FooTests.java
        "tests/",
        "test/",
        "__tests__/",
    )

    code_files = []
    test_files = []
    for f in files:
        normalized = f.replace("\\", "/")
        _, ext = os.path.splitext(f)
        if ext.lower() not in code_exts:
            continue
        if any(p in normalized for p in test_patterns):
            test_files.append(f)
        else:
            code_files.append(f)

    if not code_files:
        return True, "No source code files changed — TDD check skipped."
    if test_files:
        return (
            True,
            f"TDD OK: {len(test_files)} test file(s) modified alongside "
            f"{len(code_files)} source file(s).",
        )
    return False, (
        f"{len(code_files)} source file(s) changed but no test files modified. "
        "TDD requires tests to be written/updated alongside code changes."
    )
