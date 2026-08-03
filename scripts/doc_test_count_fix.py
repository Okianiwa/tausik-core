"""Rewrite drifted test-count references in tracked docs.

Why this exists: `gen_doc_constants.py` regenerated `constants.json` and stopped
there, while its own `--check` also judges the badges in README.md /
README.ru.md. So the remediation line printed by `check_docs`
("run `python scripts/gen_doc_constants.py` and re-commit") left the badges
behind — and, because the regenerated `test_count` had just moved, it *created*
a `--check` failure that did not exist a moment earlier. Following the advice
made things worse, which is the failure mode task
`remediation-advice-does-not-remediate` exists to close.

Rewrites happen only OUTSIDE fenced code blocks: numbers inside ``` fences are
worked examples, and bumping them would corrupt documentation to satisfy a
counter. `gen_doc_constants._strip_fenced_blocks` cannot be reused for this — it
preserves line count, not byte offsets, so match positions taken from the
stripped text do not address the original. Segmenting the text keeps both the
fences intact and the offsets true.
"""

from __future__ import annotations

import re
from pathlib import Path


def _segments(text: str, fence_re: re.Pattern[str]) -> list[tuple[str, bool]]:
    """Split into (chunk, is_fenced) pieces, in order, losing nothing."""
    out: list[tuple[str, bool]] = []
    pos = 0
    for m in fence_re.finditer(text):
        if m.start() > pos:
            out.append((text[pos : m.start()], False))
        out.append((m.group(0), True))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], False))
    return out


def _replace_capture(m: re.Match[str], expected: int) -> str:
    """Swap only capture group 1, keeping the rest of the match verbatim.

    Works for any single-group pattern, so the badge URL, the badge label and
    the prose sentence all go through one code path rather than three
    hand-written rewrites that can drift apart.
    """
    whole = m.group(0)
    start = m.start(1) - m.start(0)
    end = m.end(1) - m.start(0)
    return whole[:start] + str(expected) + whole[end:]


def fix_test_counts(
    repo_root: Path,
    expected: int,
    targets: tuple[str, ...],
    patterns: tuple[tuple[re.Pattern[str], str], ...],
    fence_re: re.Pattern[str],
) -> list[str]:
    """Update every drifted test-count ref. Returns the paths actually changed.

    Idempotent: a file already carrying `expected` is not rewritten, so running
    the generator twice does not churn mtimes or dirty a clean worktree.
    """
    changed: list[str] = []
    for rel in targets:
        path = repo_root / rel
        if not path.is_file():
            continue
        original = path.read_text(encoding="utf-8")
        rebuilt: list[str] = []
        for chunk, fenced in _segments(original, fence_re):
            if not fenced:
                for pattern, _label in patterns:
                    chunk = pattern.sub(lambda m: _replace_capture(m, expected), chunk)
            rebuilt.append(chunk)
        updated = "".join(rebuilt)
        if updated != original:
            # newline="" so the round trip cannot rewrite line endings as a side
            # effect — this repo has no .gitattributes and core.autocrlf=true,
            # which already flips endings on its own (task
            # repo-missing-gitattributes-autocrlf).
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(updated)
            changed.append(rel)
    return changed
