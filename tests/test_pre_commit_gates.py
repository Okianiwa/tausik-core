"""Commit gates must actually reach git.

Before this, `.git/hooks/` held only `*.sample`: `gate_runner.py` supported the
`commit` trigger, but nothing invoked it, so "no commit without gates" was
enforced by agent discipline alone. These tests cover the wiring — staged
content in, gate_runner out, exit code respected — and the install path.

The gates themselves (ruff/filesize) are deliberately out of scope: a stub
gate_runner stands in, so a change in gate semantics cannot make these red.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "hooks"))
sys.path.insert(0, str(REPO_ROOT / "bootstrap"))

import pre_commit_gates  # noqa: E402
from bootstrap_git_hooks import install_git_hooks, uninstall_git_hooks  # noqa: E402

# Fails the commit when any staged file it is handed exceeds 400 lines.
STUB_GATE_RUNNER = """\
import sys
files = sys.argv[sys.argv.index("--files") + 1:]
bad = [f for f in files if len(open(f, encoding="utf-8", errors="replace").readlines()) > 400]
if bad:
    print("STUB BLOCK: " + ", ".join(bad))
    sys.exit(1)
print("STUB PASS")
"""


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
    (r / "scripts").mkdir(parents=True)
    _git(r.parent, "init", "-q", str(r))
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    (r / "scripts" / "gate_runner.py").write_text(STUB_GATE_RUNNER, encoding="utf-8")
    return r


def _run_hook(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "hooks" / "pre_commit_gates.py")],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )


def _write(repo: Path, name: str, lines: int) -> Path:
    p = repo / name
    p.write_text("\n".join(f"a{i}={i}" for i in range(lines)) + "\n", encoding="utf-8")
    return p


class TestGatesReachGit:
    def test_oversized_staged_file_blocks(self, repo: Path) -> None:
        _write(repo, "big.py", 500)
        _git(repo, "add", "big.py")
        result = _run_hook(repo)
        assert result.returncode == 1
        assert "COMMIT BLOCKED" in result.stderr

    def test_clean_staged_file_passes(self, repo: Path) -> None:
        """A gate that blocks everything gets deleted by the first annoyed human."""
        _write(repo, "small.py", 10)
        _git(repo, "add", "small.py")
        assert _run_hook(repo).returncode == 0

    def test_nothing_staged_passes(self, repo: Path) -> None:
        _write(repo, "loose.py", 500)  # not staged
        assert _run_hook(repo).returncode == 0


class TestJudgesTheIndexNotTheWorktree:
    """`git add` a clean version, keep editing — the commit is of the index."""

    def test_dirty_worktree_over_clean_index_passes(self, repo: Path) -> None:
        _write(repo, "f.py", 10)
        _git(repo, "add", "f.py")
        _write(repo, "f.py", 900)  # worktree violates, index does not
        assert _run_hook(repo).returncode == 0

    def test_clean_worktree_over_dirty_index_blocks(self, repo: Path) -> None:
        _write(repo, "f.py", 900)
        _git(repo, "add", "f.py")
        _write(repo, "f.py", 10)  # worktree clean, index violates
        assert _run_hook(repo).returncode == 1

    def test_materialized_content_comes_from_index(self, repo: Path, tmp_path: Path) -> None:
        _write(repo, "f.py", 10)
        _git(repo, "add", "f.py")
        _write(repo, "f.py", 900)

        dest = tmp_path / "out"
        dest.mkdir()
        present = pre_commit_gates.materialize_index(repo, ["f.py"], dest)

        assert present == ["f.py"]
        assert len((dest / "f.py").read_text(encoding="utf-8").splitlines()) == 10

    def test_relative_layout_is_preserved(self, repo: Path, tmp_path: Path) -> None:
        """filesize exempt_files and ruff per-file-ignores are path-scoped."""
        (repo / "pkg").mkdir()
        _write(repo, "pkg/mod.py", 5)
        _git(repo, "add", "pkg/mod.py")

        dest = tmp_path / "out"
        dest.mkdir()
        pre_commit_gates.materialize_index(repo, ["pkg/mod.py"], dest)
        assert (dest / "pkg" / "mod.py").is_file()


class TestCleanupNeverDecidesTheCommit:
    """Discarding the staged tree must not outrank the gates' verdict.

    The teardown used to sit on the way out of a `with TemporaryDirectory(...)`
    block that closed *before* the return code was read. On Windows `rmtree`
    fails for reasons that have nothing to do with the staged code — an open
    handle, an antivirus scan, a read-only file — and that exception replaced
    a passing run with a rejected commit plus a `tempfile` traceback.
    """

    @pytest.fixture
    def broken_cleanup(self, monkeypatch):
        def _boom(path, *args, **kwargs):
            raise PermissionError(f"WinError 32-style lock on {path}")

        monkeypatch.setattr(pre_commit_gates.shutil, "rmtree", _boom)

    def _main_in(self, repo: Path, monkeypatch) -> int:
        monkeypatch.chdir(repo)
        return pre_commit_gates.main()

    def test_green_gates_survive_a_failed_cleanup(
        self, repo: Path, monkeypatch, broken_cleanup, capsys
    ) -> None:
        _write(repo, "small.py", 10)
        _git(repo, "add", "small.py")
        assert self._main_in(repo, monkeypatch) == 0
        assert "could not remove temp tree" in capsys.readouterr().err

    def test_red_gates_survive_a_failed_cleanup(
        self, repo: Path, monkeypatch, broken_cleanup
    ) -> None:
        """The mirror case: a lost verdict in either direction is a lost gate."""
        _write(repo, "big.py", 500)
        _git(repo, "add", "big.py")
        assert self._main_in(repo, monkeypatch) == 1

    def test_early_return_survives_a_failed_cleanup(
        self, repo: Path, monkeypatch, broken_cleanup
    ) -> None:
        """`return 0` for an empty index is a verdict too, decided before teardown."""
        _write(repo, "f.py", 10)
        _git(repo, "add", "f.py")
        monkeypatch.setattr(pre_commit_gates, "materialize_index", lambda *a, **kw: [])
        assert self._main_in(repo, monkeypatch) == 0

    def test_warning_names_the_leaked_path(
        self, repo: Path, monkeypatch, broken_cleanup, capsys
    ) -> None:
        """A silent leak is a silent error — the user must be able to find it."""
        _write(repo, "small.py", 10)
        _git(repo, "add", "small.py")
        self._main_in(repo, monkeypatch)
        assert "tausik-precommit-" in capsys.readouterr().err

    def test_successful_cleanup_removes_the_tree(self, repo: Path, monkeypatch) -> None:
        """CONTROL ON A KNOWN ANSWER: a fix that never cleans up is not a fix."""
        seen: list[str] = []
        real_rmtree = pre_commit_gates.shutil.rmtree

        def _record(path, *args, **kwargs):
            seen.append(str(path))
            return real_rmtree(path, *args, **kwargs)

        monkeypatch.setattr(pre_commit_gates.shutil, "rmtree", _record)
        _write(repo, "small.py", 10)
        _git(repo, "add", "small.py")

        assert self._main_in(repo, monkeypatch) == 0
        assert len(seen) == 1 and "tausik-precommit-" in seen[0]
        assert not Path(seen[0]).exists(), "temp tree must actually be gone"

    def test_discard_tree_never_raises(self, monkeypatch) -> None:
        """Not just OSError: anything escaping here would replace the verdict."""

        def _boom(path, *args, **kwargs):
            raise RuntimeError("something exotic")

        monkeypatch.setattr(pre_commit_gates.shutil, "rmtree", _boom)
        pre_commit_gates.discard_tree("/nonexistent")  # must not raise


class TestBypass:
    def test_env_skip_allows_commit(self, repo: Path) -> None:
        _write(repo, "big.py", 500)
        _git(repo, "add", "big.py")
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "hooks" / "pre_commit_gates.py")],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "TAUSIK_SKIP_COMMIT_GATES": "1"},
            check=False,
        )
        assert result.returncode == 0

    def test_outside_git_repo_is_noop(self, tmp_path: Path) -> None:
        assert _run_hook(tmp_path).returncode == 0


class TestInstall:
    def test_installs_executable_hook(self, repo: Path) -> None:
        assert install_git_hooks(repo) == ["pre-commit"]
        hook = repo / ".git" / "hooks" / "pre-commit"
        assert hook.is_file()
        assert "TAUSIK" in hook.read_text(encoding="utf-8")
        if os.name != "nt":
            assert os.access(hook, os.X_OK)

    def test_is_idempotent(self, repo: Path) -> None:
        install_git_hooks(repo)
        first = (repo / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")
        assert install_git_hooks(repo) == ["pre-commit"]
        assert (repo / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8") == first

    def test_never_clobbers_a_foreign_hook(self, repo: Path) -> None:
        hook = repo / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
        install_git_hooks(repo)
        assert hook.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"

    def test_uninstall_leaves_foreign_hook(self, repo: Path) -> None:
        hook = repo / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
        assert uninstall_git_hooks(repo) == []
        assert hook.is_file()

    def test_uninstall_removes_own_hook(self, repo: Path) -> None:
        install_git_hooks(repo)
        assert uninstall_git_hooks(repo) == ["pre-commit"]
        assert not (repo / ".git" / "hooks" / "pre-commit").exists()

    def test_non_git_dir_is_noop(self, tmp_path: Path) -> None:
        assert install_git_hooks(tmp_path) == []

    def test_worktree_gitdir_file_resolves(self, repo: Path, tmp_path: Path) -> None:
        """In a worktree `.git` is a file pointing at the real gitdir."""
        fake = tmp_path / "wt"
        fake.mkdir()
        real_gitdir = tmp_path / "realgit"
        (real_gitdir / "hooks").mkdir(parents=True)
        (fake / ".git").write_text(f"gitdir: {real_gitdir}\n", encoding="utf-8")
        assert install_git_hooks(fake) == ["pre-commit"]
        assert (real_gitdir / "hooks" / "pre-commit").is_file()
