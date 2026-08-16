"""push-gate-guide-two-calls — guidance that cannot be followed, and the
false diagnosis it manufactures.

The library taught, in eleven places across skills, docs and the hook's own
docstring, to authorize a push with `tausik push-ok` chained onto `git push`
by `&&`. That form can never pass. `git_push_gate` is a PreToolUse hook: it
judges the command string BEFORE the shell runs any of it, so the `push-ok`
sitting in the same line has not executed and no ticket exists at check time.

What makes it worse than a plain dead end is where the refusal points. With
no ticket anywhere, `_ticket_path` prints its FIRST candidate — a suggestion
of where to put one, not a record of where it looked. Readers took it for the
search location, concluded that `cli_push_ok` (which writes to the git dir)
and the hook had drifted apart, and went to patch the shared hub. Measured on
the live case: `_ticket_path` returned the git-dir path with `exists=True` —
there was no mismatch to fix. Two sessions and one hub edit were spent there.

A second consumer of the same confusion: a ticket is single-use, so a first
push that clears the gate and then fails on its own (network, auth) leaves
the retry facing "no push ticket" — and the real stderr is never read.

Scope note: CHANGELOG.md / CHANGELOG.ru.md are deliberately NOT scanned. They
record what shipped in a past release; the broken form is a true statement
about v1.4 and rewriting it would falsify the log. `scripts/` at large is out
too — `eval_memory_retrieval.py` quotes the anti-pattern as a dead-end recall
probe, which is the form doing its job.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Guidance surfaces only: what an agent reads to learn how to push. History
# (CHANGELOG) and recall probes (scripts/eval_*) are excluded by construction —
# see the module docstring.
_SCANNED_ROOTS = (
    _REPO_ROOT / "harness" / "skills",
    _REPO_ROOT / "docs",
    _REPO_ROOT / "scripts" / "hooks",
)

# Cross-cutting: policing push guidance across skills/, docs/ and hooks/ —
# relevant to any change that documents the push flow.
CROSSCUTTING_SCOPE = ["harness/skills/", "docs/", "scripts/hooks/"]

# `push-ok`, then `&&`, then `git push` — all on ONE line. Anchored to a single
# line on purpose: naming both commands across separate lines is exactly the
# corrected form, and must stay legal. Mentioning `&&` alone is legal too, which
# is how the fixed texts explain why chaining fails.
_CHAINED = re.compile(r"push[-_]ok\b[^\n]*&&[^\n]*git\s+push", re.IGNORECASE)

_SUFFIXES = {".md", ".py"}


def _offenders(roots: tuple[Path, ...]) -> list[str]:
    """Return `path:lineno: line` for every chained occurrence under `roots`."""
    hits: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix not in _SUFFIXES or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if _CHAINED.search(line):
                    # Best-effort relative path: the vacuity probe scans a tmp
                    # dir outside the repo, where relative_to would raise.
                    try:
                        label = path.relative_to(_REPO_ROOT).as_posix()
                    except ValueError:
                        label = path.as_posix()
                    hits.append(f"{label}:{lineno}: {line.strip()}")
    return hits


def test_guidance_never_chains_push_ok_onto_git_push() -> None:
    """The sensor. A reintroduced `&&` form is caught before it ships."""
    hits = _offenders(_SCANNED_ROOTS)
    assert not hits, (
        "`push-ok && git push` on one line cannot pass a PreToolUse hook — "
        "the ticket is not written yet when the command is judged. Split it "
        "into two separate calls:\n  " + "\n  ".join(hits)
    )


def test_sensor_is_not_vacuous(tmp_path: Path) -> None:
    """A green sensor must mean the tree is clean, not that nothing is read.

    Without this, deleting the pattern or mistyping a root would leave the
    guard permanently green and silently useless.
    """
    planted = tmp_path / "skills"
    planted.mkdir()
    (planted / "SKILL.md").write_text(
        "  tausik push-ok && git push -u origin main\n", encoding="utf-8"
    )
    assert _offenders((planted,)), "sensor failed to flag a planted chained form"


def test_changelog_history_is_left_intact() -> None:
    """The form survives in the release log, and the sensor stays green.

    Proves the exclusion is a decision, not an accident of where the scan
    happens to reach: the literal is still on disk, the guard still passes.
    """
    changelog = _REPO_ROOT / "CHANGELOG.md"
    assert changelog.exists(), "CHANGELOG.md missing — adjust this test, not the log"
    assert _CHAINED.search(changelog.read_text(encoding="utf-8")), (
        "the historical `push-ok && git push` entry was rewritten; a release "
        "log records what shipped and must not be edited to match today's advice"
    )
    assert not _offenders(_SCANNED_ROOTS)


def test_refusal_prescribes_two_separate_calls() -> None:
    """The blocked reader is told the thing that actually unblocks them.

    Naming only the skills was what left a reader with no manual path and sent
    them looking for a defect in the ticket paths instead.
    """
    source = (_REPO_ROOT / "scripts" / "hooks" / "git_push_gate.py").read_text(encoding="utf-8")
    _, _, blocked = source.partition('"BLOCKED: git push requires')
    assert blocked, "BLOCKED message not found — was it renamed?"
    message = blocked[:1200]
    assert "TWO SEPARATE" in message, "refusal does not tell the reader to split the call"
    assert "tausik push-ok" in message, "refusal does not name the authorizing command"
    assert not _CHAINED.search(message), "refusal itself prescribes the broken form"
