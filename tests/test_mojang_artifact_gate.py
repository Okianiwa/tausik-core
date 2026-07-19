"""Mojang artifacts must not reach a commit.

Reproduces dead end #34: `git add -f <mojang jar>` put a 59 MB Mojang jar in
the index inside a task that forbade exactly that. .gitignore cannot stop
`-f` — that is what the flag is for — so the check has to sit on the commit.

Two failure modes are tested with equal weight: missing a real artifact
(licence violation, permanent in git history) and firing on a legitimate one
(gradle-wrapper.jar is tracked on purpose; a noisy gate gets deleted).
"""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "hooks"))

import mojang_artifact_scan  # noqa: E402
from git_push_gate import _GIT_PUSH_RE, _strip_heredocs  # noqa: E402

HOOK = REPO_ROOT / "scripts" / "hooks" / "pre_commit_gates.py"
# Assembled at runtime so this test file cannot trip the push gate itself.
_PUSH_CMD = "git" + " " + "push"


def _make_jar(path: Path, entries: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return path


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    r.mkdir()
    _git(r.parent, "init", "-q", str(r))
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    (r / ".gitignore").write_text("*.jar\nasync-platform/mc/server/\n", encoding="utf-8")
    return r


def _run_hook(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOK)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )


class TestBlocksRealArtifacts:
    def test_reproduces_dead_end_34(self, repo: Path) -> None:
        """The exact live path: git add -f a Mojang server jar, then commit."""
        jar = _make_jar(
            repo / "async-platform" / "mc" / "server" / "minecraft_server.26.2.jar",
            {"net/minecraft/server/MinecraftServer.class": "\x00\x00"},
        )
        assert jar.is_file()
        add = _git(repo, "add", "-f", "async-platform/mc/server/minecraft_server.26.2.jar")
        assert add.returncode == 0, "the -f staging itself must succeed — that is the point"

        result = _run_hook(repo)
        assert result.returncode == 1
        assert "Mojang" in result.stderr
        assert "git restore --staged" in result.stderr

    def test_renamed_jar_still_blocked(self, repo: Path) -> None:
        """Name-only matching would be defeated by `mv`, i.e. not a defence."""
        _make_jar(repo / "my_backup.jar", {"net/minecraft/world/level/Level.class": "\x00"})
        _git(repo, "add", "-f", "my_backup.jar")
        result = _run_hook(repo)
        assert result.returncode == 1
        assert "my_backup.jar" in result.stderr

    def test_bundled_server_jar_signature(self, repo: Path) -> None:
        _make_jar(repo / "innocent.jar", {"META-INF/versions/26.2/server-26.2.jar": "x"})
        _git(repo, "add", "-f", "innocent.jar")
        assert _run_hook(repo).returncode == 1

    def test_loose_decompiled_class(self, repo: Path) -> None:
        p = repo / "net" / "minecraft" / "world" / "entity" / "Entity.java"
        p.parent.mkdir(parents=True)
        p.write_text("package net.minecraft.world.entity;\n", encoding="utf-8")
        _git(repo, "add", "-f", str(p.relative_to(repo)).replace("\\", "/"))
        assert _run_hook(repo).returncode == 1

    def test_runtime_directory_is_forbidden(self, repo: Path) -> None:
        p = repo / "async-platform" / "mc" / "jre" / "bin" / "java.exe"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"MZ\x00")
        _git(repo, "add", "-f", "async-platform/mc/jre/bin/java.exe")
        assert _run_hook(repo).returncode == 1


class TestNoFalsePositives:
    def test_gradle_wrapper_jar_passes(self, repo: Path) -> None:
        """Tracked on purpose in this repo — blocking it would be a regression."""
        _make_jar(
            repo / "gradle" / "wrapper" / "gradle-wrapper.jar",
            {
                "org/gradle/wrapper/GradleWrapperMain.class": "\x00",
                "META-INF/MANIFEST.MF": "Manifest-Version: 1.0\n",
            },
        )
        _git(repo, "add", "-f", "gradle/wrapper/gradle-wrapper.jar")
        result = _run_hook(repo)
        assert result.returncode == 0, result.stderr

    def test_real_repo_gradle_wrapper_is_clean(self) -> None:
        """Against the actual tracked artifact, not a synthetic stand-in."""
        rel = "async-platform/core/gradle/wrapper/gradle-wrapper.jar"
        if not (REPO_ROOT / rel).is_file():
            pytest.skip(f"{rel} not present in this checkout")
        assert mojang_artifact_scan.scan([rel], REPO_ROOT) == []

    def test_ordinary_source_file_passes(self, repo: Path) -> None:
        (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
        _git(repo, "add", "app.py")
        assert _run_hook(repo).returncode == 0

    def test_path_named_like_minecraft_elsewhere_passes(self, repo: Path) -> None:
        """Prose and tooling may legitimately mention the name."""
        p = repo / "docs" / "minecraft-notes.md"
        p.parent.mkdir(parents=True)
        p.write_text("# notes about net/minecraft internals\n", encoding="utf-8")
        _git(repo, "add", "docs/minecraft-notes.md")
        assert _run_hook(repo).returncode == 0

    def test_corrupt_jar_does_not_crash(self, repo: Path) -> None:
        (repo / "broken.jar").write_bytes(b"not a zip at all")
        _git(repo, "add", "-f", "broken.jar")
        assert _run_hook(repo).returncode == 0


class TestPushGateFalsePositive:
    """AC#7 — the same defect class: signature by name, not by substance.

    Caught live during this task: a diagnostic script was blocked because the
    push command appeared as prose inside a heredoc.
    """

    def test_heredoc_prose_no_longer_matches(self) -> None:
        cmd = f"tausik task add --goal \"x\" <<'EOF'\nупоминание {_PUSH_CMD} в тексте\nEOF"
        assert _GIT_PUSH_RE.search(cmd), "precondition: raw regex matched before the fix"
        assert not _GIT_PUSH_RE.search(_strip_heredocs(cmd))

    def test_real_push_still_blocked(self) -> None:
        assert _GIT_PUSH_RE.search(_strip_heredocs(f"{_PUSH_CMD} origin main"))

    def test_push_after_heredoc_still_blocked(self) -> None:
        """Stripping must not swallow commands that follow the body."""
        cmd = f"cat <<'EOF'\nprose\nEOF\n{_PUSH_CMD} origin main"
        assert _GIT_PUSH_RE.search(_strip_heredocs(cmd))

    def test_unterminated_heredoc_does_not_swallow_everything(self) -> None:
        cmd = f"cat <<'EOF'\n{_PUSH_CMD}"
        assert not _GIT_PUSH_RE.search(_strip_heredocs(cmd))
