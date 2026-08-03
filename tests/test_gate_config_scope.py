"""A config-driven checker must not block a slice it never looked at.

Task mypy-gate-false-block-on-excluded-slice. The gate `mypy
--ignore-missing-imports` names no inputs — it reads `[tool.mypy] files/exclude`
from pyproject.toml. On the commit trigger the gates judge a temp tree holding
ONLY staged content, so a commit landing entirely outside that scope leaves mypy
with no sources and it exits with a usage error, blocking a commit it never
type-checked.

The first fix (cd9db84) matched mypy's error prose and missed a third wording;
measured on mypy 2.3.0:

    slice has scripts/hooks/ only -> "There are no .py[i] files in directory 'scripts'"
    slice has no scripts/ at all  -> "Cannot read file 'scripts': No such file or directory"

so any commit touching only docs/tests/requirements was blocked. The verdict now
comes from the config, before the run.
"""

import os
import shutil
import subprocess
import sys

import pytest

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, SCRIPTS_DIR)

from gate_config_scope import slice_intersects_config_scope  # noqa: E402
from gate_runner import (  # noqa: E402
    _NOTHING_TO_CHECK_SENTINEL,
    format_results,
    run_command_gate,
)

# Bound at import time, before conftest's autouse `_mock_run_gates` replaces the
# module attribute with a (True, []) stub. This name still points at the real
# implementation, which is the thing under test here.
from gate_runner import run_gates as real_run_gates  # noqa: E402

PYPROJECT = '[tool.mypy]\nfiles = ["scripts"]\nexclude = ["scripts/hooks/"]\n'

# The gate as shipped in default_gates.py, but via `python -m` so the test does
# not depend on a `mypy` launcher being on PATH. Resolution must see through it.
MYPY_GATE = {
    "command": "python -m mypy --ignore-missing-imports",
    "file_extensions": [".py"],
}

HOOKS_ONLY = ["scripts/hooks/task_gate.py"]
NO_SCRIPTS_ROOT = ["requirements.txt", "tests/test_requirements_pins.py"]
IN_SCOPE = ["scripts/gate_runner.py"]
MIXED = ["scripts/hooks/task_gate.py", "scripts/gate_runner.py"]


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A cwd whose pyproject declares mypy's scope, like the materialized tree."""
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestScopeResolution:
    """AC1 — the answer is read from [tool.mypy], never from tool output."""

    def test_excluded_subtree_is_out_of_scope(self, project):
        assert slice_intersects_config_scope(MYPY_GATE, HOOKS_ONLY) is False

    def test_slice_missing_the_configured_root_is_out_of_scope(self, project):
        assert slice_intersects_config_scope(MYPY_GATE, NO_SCRIPTS_ROOT) is False

    def test_file_under_the_configured_root_is_in_scope(self, project):
        assert slice_intersects_config_scope(MYPY_GATE, IN_SCOPE) is True

    def test_one_in_scope_file_is_enough(self, project):
        """AC4 — the skip keys on an EMPTY intersection, not on hooks appearing."""
        assert slice_intersects_config_scope(MYPY_GATE, MIXED) is True

    def test_windows_separators_resolve_the_same(self, project):
        assert slice_intersects_config_scope(MYPY_GATE, [r"scripts\hooks\x.py"]) is False
        assert slice_intersects_config_scope(MYPY_GATE, [r"scripts\x.py"]) is True

    def test_non_python_files_never_count_as_scope(self, project):
        assert slice_intersects_config_scope(MYPY_GATE, ["scripts/notes.md"]) is False

    def test_pyi_stubs_count(self, project):
        assert slice_intersects_config_scope(MYPY_GATE, ["scripts/x.pyi"]) is True

    def test_directory_exclude_prunes_the_subtree(self, project, tmp_path):
        """An anchored pattern naming the dir must still prune files beneath it."""
        (tmp_path / "pyproject.toml").write_text(
            '[tool.mypy]\nfiles = ["scripts"]\nexclude = ["^scripts/hooks/$"]\n',
            encoding="utf-8",
        )
        assert slice_intersects_config_scope(MYPY_GATE, HOOKS_ONLY) is False


class TestNoOpinion:
    """Unknown ground -> run the gate. A skip is never the safe default."""

    def test_command_naming_its_own_inputs_is_not_config_scoped(self, project):
        gate = {"command": "ruff check {files}"}
        assert slice_intersects_config_scope(gate, NO_SCRIPTS_ROOT) is None

    def test_unknown_tool_has_no_resolver(self, project):
        gate = {"command": "some-linter --strict"}
        assert slice_intersects_config_scope(gate, NO_SCRIPTS_ROOT) is None

    def test_missing_pyproject_yields_no_opinion(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert slice_intersects_config_scope(MYPY_GATE, NO_SCRIPTS_ROOT) is None

    def test_config_without_files_key_yields_no_opinion(self, tmp_path, monkeypatch):
        (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        assert slice_intersects_config_scope(MYPY_GATE, NO_SCRIPTS_ROOT) is None

    def test_unreadable_config_yields_no_opinion(self, tmp_path, monkeypatch):
        (tmp_path / "pyproject.toml").write_text("[tool.mypy\nbroken", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        assert slice_intersects_config_scope(MYPY_GATE, NO_SCRIPTS_ROOT) is None

    def test_explicit_config_scope_field_overrides_the_executable_name(self, project):
        gate = {"command": "our-mypy-wrapper --strict", "config_scope": "mypy"}
        assert slice_intersects_config_scope(gate, HOOKS_ONLY) is False


class TestTriggerScoping:
    """AC6 — the slice IS the tree only on commit."""

    def test_commit_trigger_skips_an_out_of_scope_slice(self, project):
        passed, output = run_command_gate(MYPY_GATE, HOOKS_ONLY, trigger="commit")
        assert passed is True
        assert output == _NOTHING_TO_CHECK_SENTINEL

    @pytest.mark.parametrize("trigger", ["task-done", "verify", "review", None])
    def test_other_triggers_still_run_the_checker(self, project, trigger):
        """task-done judges the real worktree: an empty intersection with
        relevant_files means the caller scoped the run, not that there is
        nothing to check. Skipping there would retire the gate silently.
        """
        passed, output = run_command_gate(MYPY_GATE, HOOKS_ONLY, trigger=trigger)
        assert output != _NOTHING_TO_CHECK_SENTINEL
        # mypy really ran here and hit its own usage error (no scripts/*.py in
        # this temp cwd) — which is exactly a block, as before.
        assert passed is False


class TestSkipIsLegible:
    """AC5 — a waived gate must say so, and say why.

    `[SKIP] mypy` on its own line, beside `[PASS] ruff`, reads as "checked,
    fine". The runner used to print `output` only for failures, so the reason
    computed here never reached anyone.
    """

    def _render(self, project):
        gate = dict(MYPY_GATE, name="mypy", severity="block", enabled=True)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("gate_runner.get_gates_for_trigger", lambda trigger, cfg: [gate])
            mp.setattr("gate_runner.load_config", lambda: {})
            passed, results = real_run_gates("commit", HOOKS_ONLY)
        return passed, results, format_results(results)

    def test_out_of_scope_slice_is_reported_as_skipped_not_passed(self, project):
        passed, results, _ = self._render(project)
        assert passed is True
        assert results[0]["skipped"] is True

    def test_the_rendered_line_names_the_tool_and_says_it_did_not_run(self, project):
        _, _, rendered = self._render(project)
        assert "[SKIP] mypy" in rendered
        assert "NOT RUN" in rendered
        assert "pyproject.toml" in rendered, "the reason must name where the scope came from"
        assert "not a passing check" in rendered


@pytest.mark.slow
class TestAgainstRealMypy:
    """AC2/AC3/AC4 end-to-end: real mypy, tree holding only the slice.

    Mirrors pre_commit_gates.py — materialize the staged paths, copy pyproject
    beside them, run the gate from that tree.
    """

    def _tree(self, tmp_path, files: dict[str, str]):
        for rel, body in files.items():
            target = tmp_path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
        return tmp_path

    CLEAN = "def f(x: int) -> int:\n    return x\n"
    BROKEN = "def f() -> int:\n    return 'not an int'\n"

    def test_hooks_only_slice_passes(self, tmp_path, monkeypatch):
        """AC2(a): scripts/ is present, but every .py in it is excluded."""
        self._tree(tmp_path, {"scripts/hooks/h.py": self.CLEAN})
        monkeypatch.chdir(tmp_path)
        passed, output = run_command_gate(MYPY_GATE, ["scripts/hooks/h.py"], trigger="commit")
        assert passed is True
        assert output == _NOTHING_TO_CHECK_SENTINEL

    def test_slice_without_scripts_dir_passes(self, tmp_path, monkeypatch):
        """AC2(b): the wording the old marker list missed — 'Cannot read file'."""
        self._tree(tmp_path, {"requirements.txt": "pytest\n", "tests/t.py": self.CLEAN})
        monkeypatch.chdir(tmp_path)
        passed, output = run_command_gate(
            MYPY_GATE, ["requirements.txt", "tests/t.py"], trigger="commit"
        )
        assert passed is True
        assert output == _NOTHING_TO_CHECK_SENTINEL

    def test_real_type_error_in_scope_still_blocks(self, tmp_path, monkeypatch):
        """AC3: curing the false block must not make the gate decorative."""
        self._tree(tmp_path, {"scripts/broken.py": self.BROKEN})
        monkeypatch.chdir(tmp_path)
        passed, output = run_command_gate(MYPY_GATE, ["scripts/broken.py"], trigger="commit")
        assert passed is False
        assert "Incompatible return value type" in output

    def test_mixed_slice_blocks(self, tmp_path, monkeypatch):
        """AC4: hooks alongside a broken in-scope file is still a block."""
        self._tree(
            tmp_path,
            {"scripts/hooks/h.py": self.CLEAN, "scripts/broken.py": self.BROKEN},
        )
        monkeypatch.chdir(tmp_path)
        passed, output = run_command_gate(
            MYPY_GATE, ["scripts/hooks/h.py", "scripts/broken.py"], trigger="commit"
        )
        assert passed is False
        assert "Incompatible return value type" in output


@pytest.mark.slow
def test_mypy_wording_is_not_load_bearing():
    """Guard for AC1 as a property, not a snapshot.

    If a future mypy renames its usage error, nothing here should care. The
    check is that the two out-of-scope slices are decided WITHOUT invoking the
    tool at all — proven by pointing the gate at a command that cannot run.
    """
    assert shutil.which("definitely-not-a-real-checker") is None
    gate = {
        "command": "definitely-not-a-real-checker --strict",
        "config_scope": "mypy",
        "file_extensions": [".py"],
    }
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "pyproject.toml"), "w", encoding="utf-8") as fh:
            fh.write(PYPROJECT)
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            passed, output = run_command_gate(gate, HOOKS_ONLY, trigger="commit")
        finally:
            os.chdir(cwd)
    assert passed is True
    assert output == _NOTHING_TO_CHECK_SENTINEL


def test_subprocess_is_available_for_the_negative_control():
    """Sanity: the slow class above needs a real interpreter to invoke mypy."""
    assert (
        subprocess.run([sys.executable, "-c", "import mypy"], capture_output=True).returncode == 0
    )
