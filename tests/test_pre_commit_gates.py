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

sys.path.insert(0, str(REPO_ROOT / "scripts"))

import gate_runner  # noqa: E402
import pre_commit_gates  # noqa: E402
from bootstrap_git_hooks import install_git_hooks, uninstall_git_hooks  # noqa: E402

# Fails the commit when any staged file it is handed exceeds 400 lines.
# Mirrors the real gate_runner's file-list contract: --files from argv,
# --files-from a NUL-separated manifest. Reports the count it received so a
# test can prove every staged path actually arrived.
STUB_GATE_RUNNER = """\
import sys
files = []
if "--files" in sys.argv:
    i = sys.argv.index("--files") + 1
    while i < len(sys.argv) and not sys.argv[i].startswith("--"):
        files.append(sys.argv[i]); i += 1
if "--files-from" in sys.argv:
    blob = open(sys.argv[sys.argv.index("--files-from") + 1], encoding="utf-8").read()
    parts = blob.split("\\0") if "\\0" in blob else blob.splitlines()
    files += [p for p in parts if p]
import os
# Sidecar next to this stub (outside the temp tree): the hook prints gate
# output only on failure, so a passing run's count has nowhere else to go.
with open(os.path.join(os.path.dirname(__file__), "received.txt"), "w") as fh:
    fh.write(str(len(files)))
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


class TestFileListSurvivesTheArgvLimit:
    """Windows caps a command line at 32767 chars; the list must not ride argv.

    Measured on this repo by binary search: 979 repo-relative paths fit, 980 do
    not (32786 chars at the boundary). Overflow surfaces as
    `FileNotFoundError: [WinError 206]` — a class name that points at a missing
    file, which is why the failure was unrecognisable before.

    The count below is deliberately past that boundary. A test sized to what
    already works would stay green on the broken version and prove nothing.
    """

    ARGV_LIMIT = 32767
    FILE_COUNT = 1000
    # Nested, realistically long paths. Short names like "m00042.py" would keep
    # 1000 files inside the limit and the test would pass on the broken version
    # too — sizing by file COUNT rather than command-line LENGTH is the trap
    # this class is meant to catch.
    DIR = "src/generated/handlers/subsystem"

    def _stage_many(self, repo: Path) -> int:
        (repo / self.DIR).mkdir(parents=True)
        paths = [f"{self.DIR}/module_definition_{i:05d}.py" for i in range(self.FILE_COUNT)]
        for rel in paths:
            _write(repo, rel, 3)
        _git(repo, "add", "-A")

        argv_chars = sum(len(p) + 1 for p in paths)
        assert argv_chars > self.ARGV_LIMIT, (
            f"test is not exercising the limit: {argv_chars} chars < {self.ARGV_LIMIT}. "
            "Raise FILE_COUNT or lengthen DIR."
        )
        return len(paths)

    def test_more_files_than_argv_can_hold(self, repo: Path) -> None:
        self._stage_many(repo)
        result = _run_hook(repo)
        assert result.returncode == 0, result.stderr
        assert "WinError" not in result.stderr

    def test_every_staged_file_reaches_the_runner(self, repo: Path) -> None:
        """Counted on the runner's side, not trusted from the caller's.

        Silently dropping paths would leave the gate green while judging less
        than was committed — the decorative-gate failure this hook exists to
        prevent, wearing a passing exit code.
        """
        n = self._stage_many(repo)
        assert _run_hook(repo).returncode == 0
        received = int((repo / "scripts" / "received.txt").read_text(encoding="utf-8"))
        staged = len(_git(repo, "diff", "--cached", "--name-only").stdout.split())
        assert received == staged == n + 1  # + scripts/gate_runner.py

    def test_oversized_file_still_blocks_past_the_limit(self, repo: Path) -> None:
        """The control: the gate must still JUDGE, not merely survive the size."""
        self._stage_many(repo)
        _write(repo, "huge.py", 500)
        _git(repo, "add", "-A")

        result = _run_hook(repo)
        assert result.returncode == 1
        assert "huge.py" in result.stderr


class TestManifestTransport:
    def test_newline_in_path_is_not_split(self, tmp_path: Path) -> None:
        """git paths may contain newlines; staged_files reads them NUL-separated.

        A line-separated manifest would turn one such path into two entries and
        gate files that do not exist.
        """
        manifest = tmp_path / "files.txt"
        weird = "src/od\nd.py"
        pre_commit_gates.write_manifest(manifest, [weird, "plain.py"])

        assert gate_runner.read_files_manifest(str(manifest)) == [weird, "plain.py"]

    def test_hand_written_line_manifest_still_works(self, tmp_path: Path) -> None:
        manifest = tmp_path / "files.txt"
        manifest.write_text("a.py\nb.py\n", encoding="utf-8")
        assert gate_runner.read_files_manifest(str(manifest)) == ["a.py", "b.py"]

    def _real_runner(self, repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        """Invoke the REAL gate_runner — the stub cannot prove its argparse wiring."""
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "gate_runner.py"), "commit", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=False,
        )

    def test_argv_files_still_judged(self, repo: Path) -> None:
        """Backward compatibility: /commit and the docs invoke --files directly.

        Asserted with a file that VIOLATES filesize, not a clean one — dropping
        the list entirely also exits 0, so a passing clean file proves nothing.
        """
        _write(repo, "huge.py", 500)
        result = self._real_runner(repo, "--files", "huge.py")
        assert result.returncode == 1, result.stdout + result.stderr
        assert "huge.py" in result.stdout

    def test_manifest_files_are_judged(self, repo: Path) -> None:
        """The mirror: --files-from must reach the gates, not merely parse."""
        _write(repo, "huge.py", 500)
        manifest = repo / "list.txt"
        pre_commit_gates.write_manifest(manifest, ["huge.py"])

        result = self._real_runner(repo, "--files-from", str(manifest))
        assert result.returncode == 1, result.stdout + result.stderr
        assert "huge.py" in result.stdout

    def test_argv_and_manifest_combine(self, repo: Path) -> None:
        _write(repo, "a.py", 3)
        _write(repo, "huge.py", 500)
        manifest = repo / "list.txt"
        pre_commit_gates.write_manifest(manifest, ["huge.py"])

        result = self._real_runner(repo, "--files", "a.py", "--files-from", str(manifest))
        assert result.returncode == 1
        assert "huge.py" in result.stdout

    def test_manifest_lives_outside_the_materialized_tree(self, repo: Path, monkeypatch) -> None:
        """Gates run with cwd=tree; a manifest inside it would look like a repo file.

        monkeypatch, not bare `os.chdir` + hand-rolled restore: an unrestored
        chdir outlives the whole pytest session (pytest only resets cwd once, at
        the very end) and then poisons `monkeypatch.chdir` in later tests, which
        capture the already-broken directory as the one to restore to.
        """
        seen: dict[str, Path] = {}
        real = pre_commit_gates.run_gates

        def _spy(root, workdir, files, manifest):
            seen["workdir"] = Path(workdir)
            seen["manifest"] = Path(manifest)
            return real(root, workdir, files, manifest)

        _write(repo, "f.py", 3)
        _git(repo, "add", "f.py")
        monkeypatch.setattr(pre_commit_gates, "run_gates", _spy)
        monkeypatch.chdir(repo)
        pre_commit_gates.main()

        assert seen["manifest"].parent == seen["workdir"].parent
        assert seen["manifest"].parent != seen["workdir"]

    def test_single_entry_manifest_is_not_read_as_lines(self, tmp_path: Path) -> None:
        """A separated one-entry manifest holds no NUL — the format must still be
        identifiable, or a lone path gets split by an unrelated rule."""
        manifest = tmp_path / "files.txt"
        pre_commit_gates.write_manifest(manifest, ["only.py"])
        # read_bytes, not read_text(newline=...): Path.read_text only grew that
        # keyword in 3.13, and the point here is the raw byte anyway.
        assert b"\0" in manifest.read_bytes()
        assert gate_runner.read_files_manifest(str(manifest)) == ["only.py"]

    def test_carriage_return_is_not_rewritten(self, tmp_path: Path) -> None:
        """Round-trip fidelity, not newline translation.

        Unreachable via git today — `verify_path()` rejects \\r and \\n in a path
        (checked against git 2.49) — so this guards the transport itself, not a
        live staging scenario.
        """
        manifest = tmp_path / "files.txt"
        payload = ["src/od\rd.py", "plain.py"]
        pre_commit_gates.write_manifest(manifest, payload)
        assert gate_runner.read_files_manifest(str(manifest)) == payload


class TestFailurePathsReportInsteadOfCrashing:
    """Every way run_gates can fail must produce a report, not a traceback.

    The original `except OSError` covered only the subprocess launch. Writing
    the manifest sat outside the try, and `subprocess.TimeoutExpired` is not an
    OSError subclass — so two realistic failures still reached the developer as
    a stack trace, the exact symptom the handler was added to remove.
    """

    def _staged(self, repo: Path) -> Path:
        _write(repo, "f.py", 3)
        _git(repo, "add", "f.py")
        return repo

    def test_manifest_write_failure_is_reported(self, repo: Path, monkeypatch) -> None:
        self._staged(repo)

        def _boom(path, files):
            raise OSError("disk full")

        monkeypatch.setattr(pre_commit_gates, "write_manifest", _boom)
        monkeypatch.chdir(repo)
        assert pre_commit_gates.main() == 1

    def test_gate_timeout_is_reported_with_its_own_wording(
        self, repo: Path, monkeypatch, capsys
    ) -> None:
        """Reusing the launch-failure text here would name the wrong cause."""
        self._staged(repo)

        real_run = pre_commit_gates._run

        def _timeout(args, **kwargs):
            # Only the gate invocation — repo_root() and staged_files() go
            # through _run too, and blanket-raising would abort before the
            # code path under test is ever reached.
            if "--files-from" in args:
                raise subprocess.TimeoutExpired(
                    cmd="gate_runner", timeout=pre_commit_gates._GATE_TIMEOUT
                )
            return real_run(args, **kwargs)

        monkeypatch.setattr(pre_commit_gates, "_run", _timeout)
        monkeypatch.chdir(repo)

        assert pre_commit_gates.main() == 1
        err = capsys.readouterr().err
        assert "exceeded" in err and str(pre_commit_gates._GATE_TIMEOUT) in err
        assert "command line" not in err, "must not blame argv for a timeout"

    def test_timeout_is_not_an_oserror(self) -> None:
        """The premise of the separate except arm — asserted, not assumed."""
        assert not issubclass(subprocess.TimeoutExpired, OSError)

    def test_clean_run_is_unaffected(self, repo: Path, monkeypatch) -> None:
        """CONTROL: broadening except must not swallow an ordinary verdict."""
        self._staged(repo)
        monkeypatch.chdir(repo)
        assert pre_commit_gates.main() == 0

    def test_blocking_run_is_unaffected(self, repo: Path, monkeypatch) -> None:
        _write(repo, "huge.py", 500)
        _git(repo, "add", "huge.py")
        monkeypatch.chdir(repo)
        assert pre_commit_gates.main() == 1


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
