"""Every remediation line must end at a passing state.

Task `remediation-advice-does-not-remediate`. Two measured failures:

  * The skipped-gate advice said "pass --relevant-files" — a flag `tausik
    verify` does not have (it takes only --task/--scope). Followed literally it
    produced an argparse error, so the agent had no way forward.
  * `check_docs` said "run gen_doc_constants.py and re-commit", but the
    generator rewrote only constants.json. Since the same run also bumped
    `test_count`, the README badges it left behind turned a previously green
    `--check` RED — the advice actively made things worse.

These tests execute the advice rather than eyeballing it: the flags named in
the text are fed to the real CLI parser, and the generator is run against a
repo seeded with drift, then re-checked.
"""

import argparse
import io
import os
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, SCRIPTS_DIR)

import gen_doc_constants as gdc  # noqa: E402
from gate_runner import _SCOPED_SKIP_SENTINEL, run_gates  # noqa: E402
from project_parser import build_parser  # noqa: E402

# `tausik <command> ... --flag` inside backticks — the form every remediation
# line in this codebase uses.
_ADVICE_INVOCATION = re.compile(r"`tausik\s+([a-z-]+(?:\s+[a-z-]+)?)((?:\s+[^`]*?)?)`")


def _flags_in(fragment: str) -> list[str]:
    return re.findall(r"(?<![\w-])--[a-z][a-z-]*", fragment)


def parser_accepts(command: str, flag: str) -> bool:
    """Does the real CLI parser accept `flag` for `command`?

    Walks the argparse tree rather than reading --help text, so a renamed flag
    is caught even if the help string lags behind.
    """
    parser = build_parser()
    node: argparse.ArgumentParser | None = parser
    for word in command.split():
        sub = next(
            (a for a in node._actions if isinstance(a, argparse._SubParsersAction)),  # noqa: SLF001
            None,
        )
        if sub is None or word not in sub.choices:
            return False
        node = sub.choices[word]
    assert node is not None
    return any(flag in (a.option_strings or []) for a in node._actions)  # noqa: SLF001


class TestParserAccepts:
    """Control on a known answer (memory #62): the checker must be able to
    answer both ways, or 'no bad flags found' means nothing."""

    def test_known_present_flag(self):
        assert parser_accepts("task update", "--relevant-files") is True

    def test_known_absent_flag(self):
        assert parser_accepts("verify", "--relevant-files") is False

    def test_unknown_command(self):
        assert parser_accepts("no-such-command", "--anything") is False


def _skip_advice() -> str:
    """The reason text the runner shows when a scoped gate is skipped."""
    gate = {
        "name": "pytest",
        "severity": "block",
        "enabled": True,
        "command": "pytest -q {test_files_for_files}",
        "trigger": ["verify"],
    }
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("gate_runner.get_gates_for_trigger", lambda trigger, cfg: [gate])
        mp.setattr("gate_runner.load_config", lambda: {})
        _passed, results = run_gates("verify", [])
    assert results and results[0].get("skipped"), "expected a skipped gate to advise on"
    return results[0]["output"]


class TestSkippedGateAdviceIsExecutable:
    """AC1 — every flag the advice names must exist on the command it names."""

    def test_advice_is_produced(self):
        assert _SCOPED_SKIP_SENTINEL not in _skip_advice()

    def test_every_named_flag_exists_on_its_command(self):
        advice = _skip_advice()
        invocations = _ADVICE_INVOCATION.findall(advice)
        assert invocations, f"advice names no runnable command: {advice!r}"
        for command, tail in invocations:
            for flag in _flags_in(tail):
                assert parser_accepts(command, flag), (
                    f"advice tells the reader to run `tausik {command} {flag}`, "
                    f"but the CLI parser rejects that flag. Full advice: {advice!r}"
                )

    def test_advice_does_not_hang_the_flag_on_verify(self):
        """The exact regression: --relevant-files attributed to `verify`."""
        advice = _skip_advice()
        assert not re.search(r"`tausik\s+verify[^`]*--relevant-files", advice), (
            "the flag lives on `task update` / `task done`, never on `verify`"
        )

    def test_skip_is_not_worded_as_a_pass(self):
        assert "SKIPPED" in _skip_advice() or "skipped" in _skip_advice()


class TestCacheHitDoesNotLaunderAnAssertedRun:
    """AC2 — a hit must say WHAT it hit.

    `record_run` writes a green on `has_real_pass OR scope='manual'`, so a
    cached row can represent an operator assertion with no gate behind it.
    Reading it back used to return a bare `passed=True status=hit gates=[]`,
    indistinguishable from a verified green — the cache laundered the skip.
    """

    @pytest.fixture
    def svc(self, tmp_path):
        from project_backend import SQLiteBackend
        from project_service import ProjectService

        service = ProjectService(SQLiteBackend(str(tmp_path / "t.db")))
        service.epic_add("e", "E")
        service.story_add("e", "s", "S")
        service.task_add("s", "t", "Task", goal="g", role="developer")
        service.task_update("t", acceptance_criteria="1. Works\n2. Fails loudly")
        service.task_start("t")
        return service

    def _seed_cache(self, svc, scope: str):
        """Record a green row the next verify will hit."""
        from service_verification import (
            _build_cache_command,
            compute_files_hash,
            record_run,
        )

        record_run(
            svc.be._conn,
            task_slug="t",
            scope=scope,
            command=_build_cache_command("verify", []),
            exit_code=0,
            summary="pytest=PASS",
            files_hash=compute_files_hash([]),
            duration_ms=1,
        )

    def test_hit_on_manual_row_is_flagged(self, svc):
        self._seed_cache(svc, "manual")
        result = svc.run_verify_for_task("t")
        assert result["status"] == "hit"
        assert result["passed"] is True
        assert result["warning"], "a hit on an asserted row must not be silent"
        assert "ASSERTED" in result["warning"].upper()

    def test_hit_on_a_real_gate_pass_is_not_flagged(self, svc):
        """Negative control: the warning must not cry wolf on honest greens."""
        self._seed_cache(svc, "standard")
        result = svc.run_verify_for_task("t")
        assert result["status"] == "hit"
        assert result["warning"] is None

    def test_the_warning_names_the_row_it_came_from(self, svc):
        self._seed_cache(svc, "manual")
        warning = svc.run_verify_for_task("t")["warning"]
        assert "manual" in warning
        row = svc.be._conn.execute(
            "SELECT id FROM verification_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert str(row["id"]) in warning, "reader must be able to find the run"


def _seed_repo(tmp_path: Path, badge: int, prose: int | None = None) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "1.4.0"\n', encoding="utf-8"
    )
    body = (
        f"# Demo\n\n[![{badge} tests]"
        f"(https://img.shields.io/badge/tests-{badge}%20passed-brightgreen.svg)](#x)\n"
    )
    if prose is not None:
        body += f"\n> The core is covered\n> by {prose} tests and is dogfooded daily.\n"
    body += "\n```\nsee tests-9999%20passed for the badge format\n```\n"
    (tmp_path / "README.md").write_text(body, encoding="utf-8")
    return tmp_path


def _run(repo: Path, *, check: bool) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = gdc.run_main(repo, check=check, skip_mcp_counts=True)
    return code, out.getvalue() + err.getvalue()


@pytest.fixture
def live_count(monkeypatch):
    """Pin the live counts so the fixture repo's drift is deterministic.

    `mcp_counts_flat` imports the MCP tool tables from `harness/` by path; the
    seeded repo has no `harness/`, and `skip_mcp_counts` only silences the
    cross-file scan, not the payload build.
    """
    monkeypatch.setattr(gdc, "count_tests", lambda *a, **k: 4242)
    monkeypatch.setattr(
        gdc,
        "mcp_counts_flat",
        lambda root: {
            "mcp_brain_tools": 7,
            "mcp_main_tools": 105,
            "mcp_project_tools": 98,
            "mcp_rag_tools": 7,
            "mcp_tools_with_optional_rag": 112,
        },
    )
    return 4242


class TestGeneratorAdviceReachesGreen:
    """AC3 — run the advice verbatim, then re-check. Green, or it is not advice."""

    def test_drifted_badge_is_red_then_fixed_then_green(self, tmp_path, live_count):
        repo = _seed_repo(tmp_path, badge=3583)
        assert _run(repo, check=True)[0] == 1, "seeded drift should fail --check"
        code, output = _run(repo, check=False)
        assert code == 0
        assert "Updated test-count refs in README.md" in output
        assert _run(repo, check=True)[0] == 0, "advice did not reach a passing state"

    def test_prose_count_is_fixed_too(self, tmp_path, live_count):
        """The third number: badge and prose disagreed inside one file."""
        repo = _seed_repo(tmp_path, badge=3583, prose=3378)
        _run(repo, check=False)
        text = (repo / "README.md").read_text(encoding="utf-8")
        assert "by 4242 tests" in text
        assert "3378" not in text
        assert _run(repo, check=True)[0] == 0

    def test_fenced_examples_are_left_alone(self, tmp_path, live_count):
        """A number inside ``` is a worked example, not a stale fact."""
        repo = _seed_repo(tmp_path, badge=3583)
        _run(repo, check=False)
        text = (repo / "README.md").read_text(encoding="utf-8")
        assert "tests-9999%20passed" in text, "rewrote a fenced code example"

    def test_second_run_changes_nothing(self, tmp_path, live_count):
        """Idempotent — otherwise every run dirties the worktree."""
        repo = _seed_repo(tmp_path, badge=3583, prose=3378)
        _run(repo, check=False)
        before = (repo / "README.md").read_text(encoding="utf-8")
        code, output = _run(repo, check=False)
        assert code == 0
        assert "Updated test-count refs" not in output
        assert (repo / "README.md").read_text(encoding="utf-8") == before

    def test_already_clean_repo_is_not_touched(self, tmp_path, live_count):
        repo = _seed_repo(tmp_path, badge=4242)
        _, output = _run(repo, check=False)
        assert "Updated test-count refs" not in output
