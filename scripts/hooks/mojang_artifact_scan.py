"""Refuse to commit Mojang-copyrighted artifacts.

Caught on ourselves (dead end #34): inside a task whose acceptance criteria
explicitly forbade committing Mojang code, the agent ran
`git add -f async-platform/mc/server/minecraft_server.26.2.jar` and a 59 MB
Mojang jar landed in the index.

`.gitignore` is not the hole. `git check-ignore` confirms the file is
ignored; `-f` exists precisely to stage ignored files. .gitignore covers
ACCIDENT (git add ., commit -a, a formatter) — roughly 99% of the risk. It
cannot cover INTENT, by design, the same way `--no-verify` deliberately
bypasses hooks.

Why a gate rather than discipline: the cost is asymmetric. Mojang code in a
public repository is not a bug fixed by the next commit — it is a licence
violation that persists in git history until the history is rewritten.
Discipline already failed on the first run, for an agent who was reading
the prohibition at that moment.

Signatures are content-first. Name-only matching would be defeated by
`mv minecraft_server.jar backup.jar`, i.e. not a defence. Conversely the
repo legitimately tracks `gradle-wrapper.jar`, so "*.jar" is not a
signature either — a gate that fires on everything gets deleted.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

# Paths that hold a downloaded server / runtime by convention.
_FORBIDDEN_PATH_RE = re.compile(
    r"(?:^|/)async-platform/mc/(?:server|jre)/",
    re.IGNORECASE,
)

# Decompiled or extracted Mojang classes committed as loose files.
_FORBIDDEN_LOOSE_RE = re.compile(r"(?:^|/)net/minecraft/.*\.(?:class|java)$", re.IGNORECASE)

# Jar/zip names that are Mojang server bundles by convention. Checked only
# to produce a clearer message — content is what actually decides.
_SUSPICIOUS_NAME_RE = re.compile(
    r"^(?:minecraft_server|server)[-_.].*\.jar$|^minecraft_server\.jar$",
    re.IGNORECASE,
)

# Entries inside an archive that mark it as Mojang server/client code.
_ARCHIVE_ENTRY_RES = (
    re.compile(r"^net/minecraft/", re.IGNORECASE),
    re.compile(r"^META-INF/versions/[^/]+/server-[^/]*\.jar$", re.IGNORECASE),
)

_ARCHIVE_SUFFIXES = frozenset({".jar", ".zip"})

# Reading every entry name of a huge archive is cheap; extracting is not.
# Cap the scan so a pathological archive cannot stall a commit.
_MAX_ENTRIES_SCANNED = 20000


class Violation:
    __slots__ = ("path", "reason")

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason

    def __str__(self) -> str:
        return f"  {self.path}\n      {self.reason}"


def _archive_violation(abs_path: Path) -> str | None:
    """Return a reason if the archive contains Mojang code, else None."""
    try:
        with zipfile.ZipFile(abs_path) as zf:
            for i, name in enumerate(zf.namelist()):
                if i >= _MAX_ENTRIES_SCANNED:
                    break
                for pattern in _ARCHIVE_ENTRY_RES:
                    if pattern.match(name):
                        return f"archive contains Mojang code: {name}"
    except (zipfile.BadZipFile, OSError):
        # Not a readable archive — nothing to assert. Name/path rules still apply.
        return None
    return None


def scan(files: list[str], root: Path) -> list[Violation]:
    """Check staged paths for Mojang artifacts.

    `files` are repo-relative; `root` is where their staged content lives
    (the temp tree materialized from the index, not the worktree).
    """
    violations: list[Violation] = []

    for rel in files:
        normalized = rel.replace("\\", "/")
        abs_path = root / rel

        if _FORBIDDEN_PATH_RE.search(normalized):
            violations.append(
                Violation(
                    normalized,
                    "lives under async-platform/mc/{server,jre}/ — downloaded Mojang "
                    "runtime, never committed",
                )
            )
            continue

        if _FORBIDDEN_LOOSE_RE.search(normalized):
            violations.append(
                Violation(normalized, "decompiled/extracted Mojang class under net/minecraft/")
            )
            continue

        if abs_path.suffix.lower() in _ARCHIVE_SUFFIXES and abs_path.is_file():
            reason = _archive_violation(abs_path)
            if reason:
                violations.append(Violation(normalized, reason))
                continue
            if _SUSPICIOUS_NAME_RE.match(abs_path.name):
                violations.append(
                    Violation(
                        normalized,
                        f"named like a Mojang server bundle ({abs_path.name})",
                    )
                )

    return violations


def format_report(violations: list[Violation]) -> str:
    body = "\n".join(str(v) for v in violations)
    return (
        "COMMIT BLOCKED: Mojang-copyrighted artifact(s) staged.\n\n"
        f"{body}\n\n"
        "Why this is blocking, not a warning: Mojang code in a public repository is a\n"
        "LICENCE VIOLATION, and it stays in git history forever — removing it later\n"
        "means rewriting history, not another commit.\n\n"
        "Fix:\n"
        "  git restore --staged <file>     # unstage (the file stays on disk)\n\n"
        "Note: .gitignore already covers these; reaching this point normally means\n"
        "`git add -f` was used. If you believe this is a false positive, say so —\n"
        "do not reach for --no-verify."
    )
