"""The bootstrap_drift commit gate must catch source-vs-deploy divergence.

The defect it guards (task fix-bootstrap-drift-deployed-scripts-stale, memory
#107) is invisible to every other signal: tests import `scripts/`, commit gates
judge `scripts/`, while the runtime executes `.claude/scripts/`. So these tests
are deliberately paranoid in both directions — a gate that fires always carries
no more information than one that never fires (memory #62), and the control case
(fresh deploy → silence) is as load-bearing here as the positive case.

Two whole classes below exist because the first implementation passed all seven
acceptance criteria while still being wrong: it blocked consumer projects it had
no business judging (TestOnlyInsideTausik), and it named a remedy that could not
clear its own block (TestIndexVersusDeployDrift).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import gate_bootstrap_drift as gbd  # noqa: E402
import gate_runner  # noqa: E402

# conftest.py autouse-mocks gate_runner.run_gates to (True, []) so the suite
# cannot recurse into pytest-in-pytest. Bound here at import time, before that
# patch is live, because a wiring test calling the stub would assert nothing
# while staying green — the precise failure mode this whole gate exists to stop.
_REAL_RUN_GATES = gate_runner.run_gates


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A TAUSIK source checkout: markers, source scripts/, deploy, .tausik/."""
    (tmp_path / "scripts" / "hooks").mkdir(parents=True)
    (tmp_path / ".claude" / "scripts" / "hooks").mkdir(parents=True)
    (tmp_path / ".tausik").mkdir()
    (tmp_path / ".tausik" / "config.json").write_text(
        json.dumps({"bootstrap": {"ide": "claude"}}), encoding="utf-8"
    )
    # Markers that identify this as TAUSIK itself rather than a consumer.
    (tmp_path / "bootstrap").mkdir()
    (tmp_path / "bootstrap" / "bootstrap.py").write_text("# generator\n", encoding="utf-8")
    (tmp_path / "harness").mkdir()
    return tmp_path


@pytest.fixture
def inside(project: Path, monkeypatch):
    """Run as the gate does under the hook: cwd + TAUSIK_DIR both resolved."""
    monkeypatch.chdir(project)
    monkeypatch.setenv("TAUSIK_DIR", str(project / ".tausik"))
    return project


def _place(project: Path, rel: str, body: str, *, deployed: str | None = None) -> None:
    """Write `rel` under source, and (unless None) its deploy counterpart."""
    src = project / rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(body, encoding="utf-8")
    if deployed is not None:
        dst = project / ".claude" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(deployed, encoding="utf-8")


def _scan(project: Path, files: list[str]) -> tuple[list[gbd.Violation], int]:
    scoped = gbd.in_scope(files, str(project))
    return gbd.scan(scoped, str(project / ".claude" / "scripts"), ".claude", str(project))


def _reasons(violations: list[gbd.Violation]) -> list[tuple[str, str, str]]:
    return [(v.path, v.reason, v.kind) for v in violations]


class TestScan:
    def test_stale_deploy_is_reported(self, inside: Path) -> None:
        _place(inside, "scripts/svc.py", "x = 2\n", deployed="x = 1\n")
        violations, compared = _scan(inside, ["scripts/svc.py"])
        assert _reasons(violations) == [
            ("scripts/svc.py", "deployed copy is stale", gbd.DEPLOY_DRIFT)
        ]
        assert compared == 1

    def test_matching_deploy_is_silent(self, inside: Path) -> None:
        """CONTROL ON A KNOWN ANSWER — the case that keeps the gate informative."""
        _place(inside, "scripts/svc.py", "x = 1\n", deployed="x = 1\n")
        assert _scan(inside, ["scripts/svc.py"]) == ([], 1)

    def test_missing_deploy_file_is_reported(self, inside: Path) -> None:
        _place(inside, "scripts/brand_new.py", "x = 1\n", deployed=None)
        violations, _ = _scan(inside, ["scripts/brand_new.py"])
        assert _reasons(violations) == [
            ("scripts/brand_new.py", "not deployed at all", gbd.DEPLOY_DRIFT)
        ]

    def test_nested_hooks_dir_maps_through(self, inside: Path) -> None:
        """copy_scripts is recursive; scripts/hooks/ must not fall through the map."""
        _place(inside, "scripts/hooks/h.py", "x = 2\n", deployed="x = 1\n")
        violations, _ = _scan(inside, ["scripts/hooks/h.py"])
        assert [v.path for v in violations] == ["scripts/hooks/h.py"]

    def test_line_ending_difference_is_not_drift(self, inside: Path) -> None:
        """git checkout-index honours core.autocrlf; bootstrap copies verbatim."""
        (inside / "scripts" / "svc.py").write_bytes(b"a = 1\r\nb = 2\r\n")
        (inside / ".claude" / "scripts" / "svc.py").write_bytes(b"a = 1\nb = 2\n")
        assert _scan(inside, ["scripts/svc.py"]) == ([], 1)

    def test_non_python_sources_are_judged_too(self, inside: Path) -> None:
        """bootstrap copies the whole tree — a stale README is the same divergence."""
        _place(inside, "scripts/README.md", "new\n", deployed="old\n")
        violations, _ = _scan(inside, ["scripts/README.md"])
        assert [v.path for v in violations] == ["scripts/README.md"]

    @pytest.mark.parametrize("rel", ["scripts/__pycache__/svc.cpython-311.pyc", "scripts/svc.pyc"])
    def test_compiled_artifacts_ignored(self, inside: Path, rel: str) -> None:
        _place(inside, rel, "new\n", deployed=None)
        assert gbd.in_scope([rel], str(inside)) == []

    def test_pycache_is_a_segment_not_a_substring(self, inside: Path) -> None:
        """copy_dir excludes the __pycache__ *directory*; a tracked file that
        merely contains the word is authored source and must stay in scope."""
        rel = "scripts/test__pycache__helpers.py"
        _place(inside, rel, "x = 1\n", deployed=None)
        assert gbd.in_scope([rel], str(inside)) == [(rel, rel)]

    def test_path_outside_scripts_ignored(self, inside: Path) -> None:
        _place(inside, "harness/tool.py", "whatever\n", deployed=None)
        assert gbd.in_scope(["harness/tool.py"], str(inside)) == []

    def test_windows_separators_normalized(self, inside: Path) -> None:
        _place(inside, "scripts/svc.py", "x = 2\n", deployed="x = 1\n")
        violations, _ = _scan(inside, [r"scripts\svc.py"])
        assert [v.path for v in violations] == ["scripts/svc.py"]

    def test_absolute_paths_stay_in_scope(self, inside: Path) -> None:
        """relevant_files arrive absolute from MCP; dropping them is a silent green."""
        _place(inside, "scripts/svc.py", "x = 2\n", deployed="x = 1\n")
        absolute = str(inside / "scripts" / "svc.py")
        assert gbd.in_scope([absolute], str(inside)) == [(absolute, "scripts/svc.py")]

    def test_absolute_path_outside_repo_ignored(self, inside: Path) -> None:
        """Outside the checkout entirely — relpath yields '..', so it is not ours."""
        stranger = inside.parent / "outside_repo" / "scripts" / "x.py"
        stranger.parent.mkdir(parents=True)
        stranger.write_text("x\n", encoding="utf-8")
        assert gbd.in_scope([str(stranger)], str(inside)) == []

    def test_unstaged_deletion_is_skipped_and_not_counted(self, inside: Path) -> None:
        """A path with no file on disk cannot be judged — and must not be counted.

        Counting it would make "never looked at it" print the same sentence as
        "compared it and it matched".
        """
        assert _scan(inside, ["scripts/gone.py"]) == ([], 0)


class TestIndexVersusDeployDrift:
    """bootstrap deploys the WORKTREE; this gate judges the INDEX.

    When those two disagree, re-bootstrapping copies the same worktree bytes
    again and the block survives it — so the remedy must say `git add`, not
    `bootstrap`, or the reader is stuck in a loop with `--no-verify` as the only
    way out.
    """

    def test_index_behind_worktree_is_classified_as_index_drift(self, inside: Path) -> None:
        # worktree == deploy (a fresh bootstrap), but the index holds something else.
        _place(inside, "scripts/svc.py", "x = 2\n", deployed="x = 2\n")
        staged_tree = inside.parent / "staged"
        (staged_tree / "scripts").mkdir(parents=True)
        (staged_tree / "scripts" / "svc.py").write_text("x = 1\n", encoding="utf-8")

        scoped = [(str(staged_tree / "scripts" / "svc.py"), "scripts/svc.py")]
        violations, _ = gbd.scan(
            scoped, str(inside / ".claude" / "scripts"), ".claude", str(inside)
        )
        assert [v.kind for v in violations] == [gbd.INDEX_DRIFT]

    def test_index_drift_remedy_is_git_add_not_bootstrap(self, inside: Path) -> None:
        report = gbd.format_report(
            [gbd.Violation("scripts/svc.py", "…", gbd.INDEX_DRIFT, ".claude")],
            [".claude"],
        )
        assert "git add" in report
        assert "bootstrap.py" not in report, "naming bootstrap here creates an unbreakable loop"

    def test_deploy_drift_remedy_is_bootstrap(self, inside: Path) -> None:
        report = gbd.format_report(
            [gbd.Violation("scripts/svc.py", "…", gbd.DEPLOY_DRIFT, ".claude")],
            [".claude"],
        )
        assert "bootstrap.py" in report

    def test_mixed_drift_names_both_remedies(self, inside: Path) -> None:
        report = gbd.format_report(
            [
                gbd.Violation("scripts/a.py", "…", gbd.INDEX_DRIFT, ".claude"),
                gbd.Violation("scripts/b.py", "…", gbd.DEPLOY_DRIFT, ".claude"),
            ],
            [".claude"],
        )
        assert "git add" in report and "bootstrap.py" in report

    def test_three_way_disagreement_needs_both_remedies(self, inside: Path) -> None:
        """staged != worktree != deploy: bootstrap alone clears one block, raises another.

        The report must say so up front. Promising "…then commit" and then
        blocking again is how a reader concludes the gate is broken.
        """
        _place(inside, "scripts/svc.py", "B\n", deployed="C\n")
        staged_tree = inside.parent / "staged_three"
        (staged_tree / "scripts").mkdir(parents=True)
        (staged_tree / "scripts" / "svc.py").write_text("A\n", encoding="utf-8")

        scoped = [(str(staged_tree / "scripts" / "svc.py"), "scripts/svc.py")]
        violations, _ = gbd.scan(
            scoped, str(inside / ".claude" / "scripts"), ".claude", str(inside)
        )
        assert [v.kind for v in violations] == [gbd.BOTH_DRIFT]

        report = gbd.format_report(violations, [".claude"])
        assert "bootstrap.py" in report and "git add" in report

    def test_pure_deploy_drift_when_worktree_matches_index(self, inside: Path) -> None:
        """The control for the case above: index == worktree means bootstrap suffices."""
        _place(inside, "scripts/svc.py", "A\n", deployed="C\n")
        staged_tree = inside.parent / "staged_pure"
        (staged_tree / "scripts").mkdir(parents=True)
        (staged_tree / "scripts" / "svc.py").write_text("A\n", encoding="utf-8")

        scoped = [(str(staged_tree / "scripts" / "svc.py"), "scripts/svc.py")]
        violations, _ = gbd.scan(
            scoped, str(inside / ".claude" / "scripts"), ".claude", str(inside)
        )
        assert [v.kind for v in violations] == [gbd.DEPLOY_DRIFT]
        assert "git add" not in gbd.format_report(violations, [".claude"])

    def test_staged_file_absent_from_worktree_is_index_drift(self, inside: Path) -> None:
        """bootstrap copies the worktree — it cannot deploy what is not there."""
        staged_tree = inside.parent / "staged2"
        (staged_tree / "scripts").mkdir(parents=True)
        (staged_tree / "scripts" / "ghost.py").write_text("x = 1\n", encoding="utf-8")

        scoped = [(str(staged_tree / "scripts" / "ghost.py"), "scripts/ghost.py")]
        violations, _ = gbd.scan(
            scoped, str(inside / ".claude" / "scripts"), ".claude", str(inside)
        )
        assert [v.kind for v in violations] == [gbd.INDEX_DRIFT]


class TestOnlyInsideTausik:
    """TAUSIK bootstraps other projects; the source→deploy invariant is local.

    A consumer with its own `scripts/` would otherwise be permanently blocked and
    told to run a `bootstrap/bootstrap.py` its checkout does not contain.
    """

    def test_consumer_project_is_not_judged(self, tmp_path: Path, monkeypatch) -> None:
        consumer = tmp_path / "consumer"
        (consumer / "scripts").mkdir(parents=True)
        (consumer / ".claude" / "scripts").mkdir(parents=True)
        (consumer / ".tausik").mkdir()
        (consumer / ".tausik" / "config.json").write_text(
            json.dumps({"bootstrap": {"ide": "claude"}}), encoding="utf-8"
        )
        (consumer / "scripts" / "etl_job.py").write_text("print('etl')\n", encoding="utf-8")

        monkeypatch.chdir(consumer)
        monkeypatch.setenv("TAUSIK_DIR", str(consumer / ".tausik"))

        passed, output = gbd.run_bootstrap_drift_gate({}, ["scripts/etl_job.py"])
        assert passed is True
        assert "Not a TAUSIK source checkout" in output

    @pytest.mark.parametrize("missing", ["bootstrap", "harness"])
    def test_both_markers_required(self, project: Path, missing: str) -> None:
        import shutil

        shutil.rmtree(project / missing)
        assert gbd.is_tausik_source(str(project)) is False

    def test_real_repo_is_recognised(self) -> None:
        """The control: the marker must actually match this checkout."""
        assert gbd.is_tausik_source(str(REPO_ROOT)) is True


class TestFailsClosed:
    def test_unresolvable_root_blocks(self, inside: Path, monkeypatch) -> None:
        """Deciding "they match" because a copy could not be found launders a failure."""
        monkeypatch.setattr(gbd, "project_root", lambda: None)
        passed, output = gbd.run_bootstrap_drift_gate({}, ["scripts/svc.py"])
        assert passed is False
        assert "Cannot resolve the project root" in output

    def test_project_root_returns_none_on_resolution_error(self, monkeypatch) -> None:
        import project_config

        def _boom() -> str:
            raise RuntimeError("no config")

        monkeypatch.setattr(project_config, "find_tausik_dir", _boom)
        assert gbd.project_root() is None

    def test_malformed_ide_config_blocks(self, inside: Path) -> None:
        """A guessed deploy dir turns a real deploy elsewhere into "none exists".

        `{"bootstrap": "cursor"}` (a string where a dict belongs) used to raise,
        get swallowed, and collapse to `.claude` — which does not exist in that
        layout, so the gate reported "nothing to compare" and passed a commit
        with genuinely stale `.cursor/scripts/`. Silent bypass of a block gate.
        """
        (inside / ".tausik" / "config.json").write_text(
            json.dumps({"bootstrap": "cursor"}), encoding="utf-8"
        )
        cursor = inside / ".cursor" / "scripts"
        cursor.mkdir(parents=True)
        (cursor / "svc.py").write_text("deployed-old\n", encoding="utf-8")
        _place(inside, "scripts/svc.py", "staged-new\n", deployed=None)

        assert gbd.deploy_dirs(str(inside)) is None
        passed, output = gbd.run_bootstrap_drift_gate({}, ["scripts/svc.py"])
        assert passed is False
        assert "Cannot read bootstrap.ide" in output

    def test_unreadable_config_blocks(self, inside: Path, monkeypatch) -> None:
        import project_config

        def _boom() -> dict:
            raise RuntimeError("corrupt config")

        monkeypatch.setattr(project_config, "load_config", _boom)
        assert gbd.deploy_dirs(str(inside)) is None

    def test_unreadable_staged_file_is_a_violation(self, inside: Path, monkeypatch) -> None:
        _place(inside, "scripts/svc.py", "x = 1\n", deployed="x = 1\n")
        monkeypatch.setattr(gbd, "_read", lambda path: None)
        violations, compared = _scan(inside, ["scripts/svc.py"])
        assert [v.reason for v in violations] == ["staged content could not be read"]
        assert compared == 0


class TestGateEntryPoint:
    def test_blocks_and_names_the_remedy(self, inside: Path) -> None:
        _place(inside, "scripts/svc.py", "x = 2\n", deployed="x = 1\n")
        passed, output = gbd.run_bootstrap_drift_gate({}, ["scripts/svc.py"])
        assert passed is False
        assert "scripts/svc.py" in output
        assert "bootstrap.py" in output, "a block must state how to unblock"

    def test_passes_on_a_fresh_deploy(self, inside: Path) -> None:
        _place(inside, "scripts/svc.py", "x = 1\n", deployed="x = 1\n")
        passed, output = gbd.run_bootstrap_drift_gate({}, ["scripts/svc.py"])
        assert passed is True
        assert "1 staged source(s) match" in output

    def test_reports_compared_count_not_scoped_count(self, inside: Path) -> None:
        """A file that never materialized must not be reported as verified."""
        _place(inside, "scripts/real.py", "x = 1\n", deployed="x = 1\n")
        passed, output = gbd.run_bootstrap_drift_gate({}, ["scripts/real.py", "scripts/ghost.py"])
        assert passed is True
        assert "1 staged source(s) match" in output, f"claimed more than it compared: {output}"

    def test_nothing_compared_says_so(self, inside: Path) -> None:
        passed, output = gbd.run_bootstrap_drift_gate({}, ["scripts/ghost.py"])
        assert passed is True
        assert "nothing was compared" in output

    def test_remedy_survives_runner_truncation(self, inside: Path) -> None:
        """gate_runner.format_results prints 5 output lines; the fix must be in them.

        Asserting on the gate's return value alone would pass while the reader
        sees a block with no way out — the message is only as good as its
        rendered form.
        """
        for i in range(12):
            _place(inside, f"scripts/svc{i}.py", "x = 2\n", deployed="x = 1\n")

        _, output = gbd.run_bootstrap_drift_gate({}, [f"scripts/svc{i}.py" for i in range(12)])
        rendered = gate_runner.format_results(
            [{"name": "bootstrap_drift", "severity": "block", "passed": False, "output": output}]
        )
        assert "bootstrap.py" in rendered, "remedy was truncated away by the runner"
        assert "12 staged source(s) differ" in rendered, "truncated report must state the total"

    def test_no_deploy_dir_is_not_a_violation(self, inside: Path) -> None:
        """Never bootstrapped → nothing deployed → nothing can be stale."""
        import shutil

        shutil.rmtree(inside / ".claude" / "scripts")
        _place(inside, "scripts/svc.py", "x = 1\n", deployed=None)
        passed, output = gbd.run_bootstrap_drift_gate({}, ["scripts/svc.py"])
        assert passed is True
        assert "No IDE deploy" in output

    def test_ide_all_checks_every_deploy(self, inside: Path) -> None:
        """--ide all deploys to several dirs; checking only .claude hides drift."""
        (inside / ".tausik" / "config.json").write_text(
            json.dumps({"bootstrap": {"ide": "all"}}), encoding="utf-8"
        )
        cursor = inside / ".cursor" / "scripts"
        cursor.mkdir(parents=True)
        (cursor / "svc.py").write_text("x = 1\n", encoding="utf-8")  # stale
        _place(inside, "scripts/svc.py", "x = 2\n", deployed="x = 2\n")  # .claude is fresh

        passed, output = gbd.run_bootstrap_drift_gate({}, ["scripts/svc.py"])
        assert passed is False, "drift in .cursor must block even when .claude is clean"
        assert ".cursor" in output

    def test_multi_deploy_counts_distinct_files_not_violations(self, inside: Path) -> None:
        """One stale file drifts against every configured dir at once.

        Counting raw violations would announce "2 staged source(s) differ" for a
        single file and send the reader hunting for one that does not exist.
        """
        (inside / ".tausik" / "config.json").write_text(
            json.dumps({"bootstrap": {"ide": "all"}}), encoding="utf-8"
        )
        cursor = inside / ".cursor" / "scripts"
        cursor.mkdir(parents=True)
        (cursor / "svc.py").write_text("old\n", encoding="utf-8")
        _place(inside, "scripts/svc.py", "new\n", deployed="old\n")

        passed, output = gbd.run_bootstrap_drift_gate({}, ["scripts/svc.py"])
        assert passed is False
        assert "1 staged source(s) differ" in output, output.splitlines()[1]

    def test_ide_deploy_dir_follows_config(self, inside: Path) -> None:
        (inside / ".tausik" / "config.json").write_text(
            json.dumps({"bootstrap": {"ide": "cursor"}}), encoding="utf-8"
        )
        cursor = inside / ".cursor" / "scripts"
        cursor.mkdir(parents=True)
        (cursor / "svc.py").write_text("x = 1\n", encoding="utf-8")
        _place(inside, "scripts/svc.py", "x = 2\n", deployed="x = 2\n")

        passed, output = gbd.run_bootstrap_drift_gate({}, ["scripts/svc.py"])
        assert passed is False
        assert ".cursor" in output, "report must name the dir it compared"

    def test_resolves_root_from_env_not_cwd(self, project: Path, monkeypatch) -> None:
        """Under pre-commit the gate runs from the staged temp tree, not the repo.

        cwd holds staged content; only TAUSIK_DIR points back at the checkout
        that owns .claude/. Resolving the deploy relative to cwd would compare
        the staged file against nothing and pass every time.
        """
        staged_tree = project.parent / "staged3"
        (staged_tree / "scripts").mkdir(parents=True)
        (staged_tree / "scripts" / "svc.py").write_text("x = 2\n", encoding="utf-8")
        _place(project, "scripts/svc.py", "x = 1\n", deployed="x = 1\n")

        monkeypatch.chdir(staged_tree)
        monkeypatch.setenv("TAUSIK_DIR", str(project / ".tausik"))

        passed, output = gbd.run_bootstrap_drift_gate({}, ["scripts/svc.py"])
        assert passed is False, "staged x=2 vs deployed x=1 must block"
        assert "scripts/svc.py" in output


class TestWiredIntoGateRunner:
    """Registration + dispatch, exercised through run_gates rather than asserted."""

    def _configure(self, project: Path, monkeypatch) -> None:
        (project / ".tausik" / "config.json").write_text(
            json.dumps(
                {
                    "bootstrap": {"ide": "claude"},
                    "gates": {
                        "ruff": {"enabled": False},
                        "filesize": {"enabled": False},
                        "bootstrap_drift": {"enabled": True},
                    },
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.chdir(project)
        monkeypatch.setenv("TAUSIK_DIR", str(project / ".tausik"))

    def test_registered_for_commit_trigger(self, project: Path, monkeypatch) -> None:
        self._configure(project, monkeypatch)
        from project_config import get_gates_for_trigger, load_config

        names = [g["name"] for g in get_gates_for_trigger("commit", load_config())]
        assert "bootstrap_drift" in names

    def test_run_gates_blocks_on_drift(self, project: Path, monkeypatch) -> None:
        self._configure(project, monkeypatch)
        _place(project, "scripts/svc.py", "x = 2\n", deployed="x = 1\n")

        all_passed, results = _REAL_RUN_GATES("commit", ["scripts/svc.py"])
        drift = next(r for r in results if r["name"] == "bootstrap_drift")
        assert drift["severity"] == "block"
        assert drift["passed"] is False
        assert all_passed is False

    def test_run_gates_clean_on_fresh_deploy(self, project: Path, monkeypatch) -> None:
        self._configure(project, monkeypatch)
        _place(project, "scripts/svc.py", "x = 1\n", deployed="x = 1\n")

        all_passed, results = _REAL_RUN_GATES("commit", ["scripts/svc.py"])
        assert all_passed is True
        assert next(r for r in results if r["name"] == "bootstrap_drift")["passed"] is True

    def test_commit_untouched_by_scripts_stays_clean(self, project: Path, monkeypatch) -> None:
        """A commit outside scripts/ must neither block nor consult the deploy."""
        self._configure(project, monkeypatch)
        (project / "docs").mkdir()
        (project / "docs" / "notes.md").write_text("hi\n", encoding="utf-8")
        # Deliberate drift elsewhere: it must not leak into an unrelated commit.
        _place(project, "scripts/svc.py", "x = 2\n", deployed="x = 1\n")

        all_passed, results = _REAL_RUN_GATES("commit", ["docs/notes.md"])
        assert all_passed is True
        drift = next(r for r in results if r["name"] == "bootstrap_drift")
        assert drift["passed"] is True
        # Must not claim a comparison it never made — "nothing to compare" and
        # "everything matches" are different facts about the deploy.
        assert "nothing to compare" in drift["output"]


class TestEscapeHatch:
    def test_env_var_short_circuits_before_any_gate(self, tmp_path: Path, monkeypatch) -> None:
        """The hatch must be what returns 0 — not the not-a-git-repo branch.

        Asserting main() == 0 in a bare tmp_path proves nothing: `repo_root()`
        returns None there and the hook exits 0 with the hatch deleted.
        """
        sys.path.insert(0, str(REPO_ROOT / "scripts" / "hooks"))
        import pre_commit_gates

        def _unreachable() -> None:
            raise AssertionError("repo_root reached — the hatch did not short-circuit")

        monkeypatch.setattr(pre_commit_gates, "repo_root", _unreachable)
        monkeypatch.chdir(tmp_path)

        monkeypatch.setenv("TAUSIK_SKIP_COMMIT_GATES", "1")
        assert pre_commit_gates.main() == 0

        monkeypatch.delenv("TAUSIK_SKIP_COMMIT_GATES")
        with pytest.raises(AssertionError, match="repo_root reached"):
            pre_commit_gates.main()


def test_gate_is_enabled_by_default() -> None:
    """An opt-in gate would leave the original defect exactly where it was."""
    from default_gates import UNIVERSAL_GATES

    gate = UNIVERSAL_GATES["bootstrap_drift"]
    assert gate["enabled"] is True
    assert gate["severity"] == "block"
    assert gate["trigger"] == ["commit"]
