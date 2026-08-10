"""Tests for the gate scope guard — gates judge only files of THIS project.

Regression origin: `task done` on a task whose code lives in another repository
was blocked by `filesize` on a 479-line file from that repo, whose own contract
exempts tests. The fix must drop foreign files WITHOUT going quiet about it, and
without loosening anything for files that do belong here.
"""

import os
import sys

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, SCRIPTS_DIR)

import gate_scope  # noqa: E402
from gate_runner import format_results, run_gates  # noqa: E402
from gate_scope import external_scope_note, split_by_project_root  # noqa: E402

_FILESIZE_ONLY = [{"name": "filesize", "severity": "block", "max_lines": 400}]


def _only_filesize(monkeypatch):
    """Pin the registry to filesize so the assertions are about scope, not config."""
    monkeypatch.setattr("gate_runner.get_gates_for_trigger", lambda trigger, cfg: _FILESIZE_ONLY)
    monkeypatch.setattr("gate_runner.load_config", lambda: {})


def _oversized(path, lines=450):
    path.write_text("x\n" * lines)
    return str(path)


class TestSplitByProjectRoot:
    def test_absolute_outside_is_dropped(self, tmp_path, monkeypatch):
        root = tmp_path / "proj"
        root.mkdir()
        monkeypatch.setattr(gate_scope, "project_root", lambda: str(root))
        inside, outside = split_by_project_root([str(tmp_path / "other" / "a.py")])
        assert inside == []
        assert outside == [str(tmp_path / "other" / "a.py")]

    def test_absolute_inside_is_kept(self, tmp_path, monkeypatch):
        root = tmp_path / "proj"
        root.mkdir()
        monkeypatch.setattr(gate_scope, "project_root", lambda: str(root))
        target = str(root / "scripts" / "a.py")
        inside, outside = split_by_project_root([target])
        assert inside == [target]
        assert outside == []

    def test_relative_paths_are_never_foreign(self, tmp_path, monkeypatch):
        """Load-bearing: the pre-commit hook runs gates from a temp tree (memory
        #105), where resolving relative paths against cwd would drop the whole
        commit out of scope and report green having checked nothing."""
        root = tmp_path / "proj"
        root.mkdir()
        monkeypatch.setattr(gate_scope, "project_root", lambda: str(root))
        monkeypatch.chdir(tmp_path / "..")
        inside, outside = split_by_project_root(["scripts/a.py", "docs/b.md"])
        assert inside == ["scripts/a.py", "docs/b.md"]
        assert outside == []

    def test_unresolvable_root_keeps_everything_in_scope(self, monkeypatch):
        """Fail-closed: a guard that cannot locate itself must not stop a check."""
        monkeypatch.setattr(gate_scope, "project_root", lambda: None)
        inside, outside = split_by_project_root(["D:/elsewhere/a.py", "scripts/b.py"])
        assert inside == ["D:/elsewhere/a.py", "scripts/b.py"]
        assert outside == []

    def test_sibling_directory_sharing_a_prefix_is_foreign(self, tmp_path, monkeypatch):
        """`/x/proj-tools` must not read as inside `/x/proj`."""
        root = tmp_path / "proj"
        root.mkdir()
        monkeypatch.setattr(gate_scope, "project_root", lambda: str(root))
        sibling = str(tmp_path / "proj-tools" / "a.py")
        inside, outside = split_by_project_root([sibling])
        assert inside == []
        assert outside == [sibling]


class TestRunGatesScope:
    def test_foreign_oversized_file_does_not_block(self, tmp_path, monkeypatch):
        _only_filesize(monkeypatch)
        root = tmp_path / "proj"
        root.mkdir()
        foreign_dir = tmp_path / "other"
        foreign_dir.mkdir()
        foreign = _oversized(foreign_dir / "bench.py")
        monkeypatch.setattr(gate_scope, "project_root", lambda: str(root))

        passed, results = run_gates("task-done", [foreign])

        assert passed is True
        fs = next(r for r in results if r["name"] == "filesize")
        assert fs["skipped"] is True, "an emptied scope must report 'verified nothing'"
        assert "NOT CHECKED HERE" in fs["output"]
        assert "bench.py" in fs["output"]

    def test_own_oversized_file_still_blocks(self, tmp_path, monkeypatch):
        """Mutation control: without this, the fix is indistinguishable from
        switching the gate off."""
        _only_filesize(monkeypatch)
        root = tmp_path / "proj"
        root.mkdir()
        own = _oversized(root / "big.py")
        monkeypatch.setattr(gate_scope, "project_root", lambda: str(root))

        passed, results = run_gates("task-done", [own])

        assert passed is False
        fs = next(r for r in results if r["name"] == "filesize")
        assert fs["passed"] is False
        assert "450 lines" in fs["output"]

    def test_mixed_scope_judges_own_and_drops_foreign(self, tmp_path, monkeypatch):
        _only_filesize(monkeypatch)
        root = tmp_path / "proj"
        root.mkdir()
        foreign_dir = tmp_path / "other"
        foreign_dir.mkdir()
        own = _oversized(root / "big.py")
        foreign = _oversized(foreign_dir / "bench.py", lines=479)
        monkeypatch.setattr(gate_scope, "project_root", lambda: str(root))

        passed, results = run_gates("task-done", [own, foreign])

        assert passed is False
        fs = next(r for r in results if r["name"] == "filesize")
        assert "big.py" in fs["output"]
        assert "bench.py" not in fs["output"], "foreign file must not appear in the verdict"
        assert "bench.py" in fs["scope_note"], "but must appear in what was NOT checked"

    def test_unresolvable_root_still_blocks_foreign_file(self, tmp_path, monkeypatch):
        """Fail-closed end to end: no root → old behaviour, not a free pass."""
        _only_filesize(monkeypatch)
        foreign = _oversized(tmp_path / "bench.py")
        monkeypatch.setattr(gate_scope, "project_root", lambda: None)

        passed, _ = run_gates("task-done", [foreign])

        assert passed is False


class TestScopeNoteIsRendered:
    """conv #127: a verdict is checked on the RENDER, not on the returned dict."""

    def test_note_prints_next_to_a_passing_gate(self, tmp_path, monkeypatch):
        _only_filesize(monkeypatch)
        root = tmp_path / "proj"
        root.mkdir()
        foreign_dir = tmp_path / "other"
        foreign_dir.mkdir()
        (root / "small.py").write_text("x\n" * 10)
        foreign = _oversized(foreign_dir / "bench.py")
        monkeypatch.setattr(gate_scope, "project_root", lambda: str(root))

        _, results = run_gates("task-done", [str(root / "small.py"), foreign])
        rendered = format_results(results)

        assert "[PASS] filesize" in rendered
        assert "NOT checked here" in rendered
        assert "bench.py" in rendered

    def test_clean_scope_renders_no_note(self, tmp_path, monkeypatch):
        _only_filesize(monkeypatch)
        root = tmp_path / "proj"
        root.mkdir()
        (root / "small.py").write_text("x\n" * 10)
        monkeypatch.setattr(gate_scope, "project_root", lambda: str(root))

        _, results = run_gates("task-done", [str(root / "small.py")])
        rendered = format_results(results)

        assert "SCOPE:" not in rendered
        assert "NOT CHECKED HERE" not in rendered


class TestExternalScopeNote:
    def test_emptied_scope_says_nothing_was_verified(self):
        note = external_scope_note(["/a/b.py"], scope_emptied=True)
        assert "verified NOTHING" in note
        assert "not a passing check" in note

    def test_partial_scope_says_what_was_left_out(self):
        note = external_scope_note(["/a/b.py"], scope_emptied=False)
        assert "NOT checked here" in note
        assert "/a/b.py" in note
