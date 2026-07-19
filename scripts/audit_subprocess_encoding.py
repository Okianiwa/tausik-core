#!/usr/bin/env python3
"""Audit: subprocess text-mode calls that decode with the host locale.

`subprocess.run(..., text=True)` without an explicit codec decodes child
output through `locale.getpreferredencoding()`. On a cp1251/cp866 Windows
host that mis-decodes UTF-8 output; worse, the failure surfaces inside the
reader thread, so the call still returns rc=0 while stdout comes back None
(commit b395a07). Locale-dependent, so CI on UTF-8 stays green and the
defect ships.

Two remedies count as protection, both already used in this repo:
  - `encoding="utf-8"` (or `errors=`/`encoding=` via any literal) on the call
  - `env={... "PYTHONIOENCODING": "utf-8" ...}` passed to the same call

Detection is AST-based, not regex: the env-var remedy hides inside a dict
literal that no substring match can read reliably.

Usage:
    python scripts/audit_subprocess_encoding.py [paths...]

Exit codes: 0 = clean, 1 = unprotected call sites found.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Attribute calls (subprocess.run) and bare names (run) alike.
_SUBPROCESS_FUNCS = frozenset({"run", "Popen", "check_output", "call", "check_call"})
_TEXT_KWARGS = frozenset({"text", "universal_newlines"})
_GUARD_KWARGS = frozenset({"encoding", "errors"})
_ENV_GUARD = "PYTHONIOENCODING"

_SKIP_DIRS = frozenset(
    {".git", "__pycache__", ".tausik", "venv", ".venv", "node_modules", "build", "dist"}
)


class Finding:
    __slots__ = ("path", "line", "func")

    def __init__(self, path: Path, line: int, func: str) -> None:
        self.path = path
        self.line = line
        self.func = func

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.func}(text=True) without encoding="


def _func_name(node: ast.Call) -> str | None:
    """Return the callee name if it looks like a subprocess entry point."""
    func = node.func
    if isinstance(func, ast.Attribute):
        name = func.attr
    elif isinstance(func, ast.Name):
        name = func.id
    else:
        return None
    return name if name in _SUBPROCESS_FUNCS else None


def _is_truthy(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _env_sets_ioencoding(node: ast.expr) -> bool:
    """True when the env argument mentions PYTHONIOENCODING anywhere inside.

    Covers the `{**os.environ, "PYTHONIOENCODING": "utf-8"}` idiom and any
    nesting of it; a variable reference cannot be resolved statically, so
    treat a bare Name as protected rather than emit a false positive.
    """
    if isinstance(node, ast.Name):
        return True
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and sub.value == _ENV_GUARD:
            return True
    return False


def scan_source(source: str, path: Path) -> list[Finding]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _func_name(node)
        if name is None:
            continue

        text_mode = False
        guarded = False
        for kw in node.keywords:
            if kw.arg in _TEXT_KWARGS and _is_truthy(kw.value):
                text_mode = True
            elif kw.arg in _GUARD_KWARGS:
                guarded = True
            elif kw.arg == "env" and _env_sets_ioencoding(kw.value):
                guarded = True
            elif kw.arg is None:
                # **kwargs splat — cannot prove absence of a guard.
                guarded = True

        if text_mode and not guarded:
            findings.append(Finding(path, node.lineno, name))
    return findings


def scan_path(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    files = [root] if root.is_file() else sorted(root.rglob("*.py"))
    for file in files:
        if any(part in _SKIP_DIRS for part in file.parts):
            continue
        try:
            source = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend(scan_source(source, file))
    return findings


def main(argv: list[str]) -> int:
    roots = [Path(a) for a in argv[1:]] or [Path("scripts"), Path("tests"), Path("bootstrap")]
    findings: list[Finding] = []
    for root in roots:
        if root.exists():
            findings.extend(scan_path(root))

    if not findings:
        print("subprocess-encoding audit: clean")
        return 0

    print(f"subprocess-encoding audit: {len(findings)} unprotected call site(s)\n")
    for f in findings:
        print(f"  {f}")
    print(
        '\nFix: add encoding="utf-8" to the call, or pass '
        'env={**os.environ, "PYTHONIOENCODING": "utf-8"}.'
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
