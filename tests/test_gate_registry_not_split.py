"""The gate registry must not depend on which copy of the code is running.

Regression guard for task `gates-registry-split-cli-vs-hook` (decision #64).
Measured before the fix: importing from `scripts/` yielded 26 gates while
importing the deployed copy under `.claude/scripts/` yielded 7, so a single
config.json produced two behaviours — `{"gates": {"tsc": {"enabled": true}}}`
was an inert stub under the CLI and a live `npx tsc` run under the commit hook.

These tests exercise the path resolvers directly rather than re-importing the
modules twice: `default_gates` builds `DEFAULT_GATES` at import time and caches
the registry in a module singleton, so a second import inside one interpreter
would answer from cache and prove nothing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import pytest  # noqa: E402

import stack_registry_paths as paths  # noqa: E402
from gate_enable_check import check_gate_enable  # noqa: E402


class TestRegistryIsAProjectProperty:
    def test_catalog_dir_ignores_the_running_copy(self, monkeypatch, tmp_path):
        """Same project, two code locations -> same catalog directory."""
        (tmp_path / ".tausik").mkdir()
        (tmp_path / "stacks").mkdir()
        monkeypatch.setenv("TAUSIK_DIR", str(tmp_path / ".tausik"))

        assert paths.catalog_stacks_dir() == str(tmp_path / "stacks")

    def test_active_dir_is_the_projects_deploy(self, monkeypatch, tmp_path):
        (tmp_path / ".tausik").mkdir()
        deploy = tmp_path / ".claude" / "stacks"
        deploy.mkdir(parents=True)
        monkeypatch.setenv("TAUSIK_DIR", str(tmp_path / ".tausik"))

        assert paths.active_builtin_dir() == str(deploy)

    def test_active_dir_falls_back_to_catalog_before_bootstrap(self, monkeypatch, tmp_path):
        """No deploy dir yet — bootstrap itself must still be able to run."""
        (tmp_path / ".tausik").mkdir()
        (tmp_path / "stacks").mkdir()
        monkeypatch.setenv("TAUSIK_DIR", str(tmp_path / ".tausik"))

        assert paths.active_builtin_dir() == str(tmp_path / "stacks")

    def test_env_var_wins_over_cwd(self, monkeypatch, tmp_path):
        """The commit hook runs gates from a temp tree; cwd is not the project.

        Without this, gates judged from the staged tree would resolve the
        registry against the temp dir and silently find no stacks at all.
        """
        project = tmp_path / "project"
        (project / ".tausik").mkdir(parents=True)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()

        monkeypatch.setenv("TAUSIK_DIR", str(project / ".tausik"))
        monkeypatch.chdir(elsewhere)

        assert paths.project_root() == str(project)

    def test_project_root_searches_upward_without_env(self, monkeypatch, tmp_path):
        project = tmp_path / "project"
        nested = project / "a" / "b"
        (project / ".tausik").mkdir(parents=True)
        nested.mkdir(parents=True)

        monkeypatch.delenv("TAUSIK_DIR", raising=False)
        monkeypatch.chdir(nested)

        assert paths.project_root() == str(project)

    def test_shipped_catalog_and_active_registry_are_both_reachable(self):
        """On this repo the two answers differ — and both must be available.

        The catalog carries every stack TAUSIK ships; the active registry
        carries what this python project deploys. Collapsing either into the
        other is what produced the split.
        """
        from stack_registry import catalog_registry, default_registry

        catalog = catalog_registry().all_stacks()
        active = default_registry().all_stacks()

        assert "python" in active
        assert {"go", "rust", "php", "typescript"} <= set(catalog)
        assert active <= catalog


class TestEnablingADeadGateIsRefused:
    """`gates enable` must not store an entry that fires on no trigger."""

    class _FakeCatalog:
        def all_stacks(self):
            return {"typescript", "python"}

        def gates_for(self, stack):
            return {"tsc": {}} if stack == "typescript" else {"pytest": {}}

    def test_gate_from_an_undeployed_stack_is_refused(self):
        refusal = check_gate_enable("tsc", {"pytest": {}}, {}, self._FakeCatalog())

        assert refusal is not None
        assert "typescript" in refusal
        # The remedy has to be in the text, not just the complaint.
        assert "bootstrap" in refusal and "command" in refusal

    def test_unknown_gate_is_refused_with_the_available_list(self):
        refusal = check_gate_enable("nope", {"pytest": {}}, {}, self._FakeCatalog())

        assert refusal is not None
        assert "pytest" in refusal

    def test_gate_in_the_active_registry_is_allowed(self):
        assert check_gate_enable("pytest", {"pytest": {}}, {}, self._FakeCatalog()) is None

    def test_user_gate_with_a_command_is_allowed(self):
        user = {"mine": {"command": "pytest -q"}}

        assert check_gate_enable("mine", {}, user, self._FakeCatalog()) is None

    def test_builtin_dispatch_gate_needs_no_command(self):
        """filesize/tdd_order/bootstrap_drift run by name, not by shell."""
        assert check_gate_enable("filesize", {}, {}, self._FakeCatalog()) is None


class TestTriggersMatchProduction:
    def test_review_is_not_a_valid_trigger(self):
        """It fired for nothing, so any gate assigned to it was unreachable."""
        from project_config import VALID_GATE_TRIGGERS
        from stack_schema import VALID_GATE_TRIGGERS as SCHEMA_TRIGGERS

        assert "review" not in VALID_GATE_TRIGGERS
        assert "review" not in SCHEMA_TRIGGERS
        assert VALID_GATE_TRIGGERS == SCHEMA_TRIGGERS

    def test_cli_accepts_every_trigger_that_can_hold_gates(self, monkeypatch, capsys):
        """Asserted on argparse's own rendered error, not on a parsed literal.

        `verify` used to be missing here while carrying the heavy gates, so
        they could not be run from the CLI at all; `review` was offered and
        fired for nothing.
        """
        import gate_runner
        from project_config import VALID_GATE_TRIGGERS

        monkeypatch.setattr(sys, "argv", ["gate_runner.py", "no-such-trigger"])
        with pytest.raises(SystemExit):
            gate_runner.main()

        rendered = capsys.readouterr().err
        for trigger in VALID_GATE_TRIGGERS:
            assert repr(trigger) in rendered
        assert "'review'" not in rendered

    def test_bandit_hangs_off_a_trigger_that_runs(self):
        from project_config import VALID_GATE_TRIGGERS
        from default_gates import UNIVERSAL_GATES

        assert set(UNIVERSAL_GATES["bandit"]["trigger"]) <= set(VALID_GATE_TRIGGERS)
        assert UNIVERSAL_GATES["bandit"]["trigger"]


def test_paths_module_has_no_import_cycle_back_into_config():
    """Measured: `from project_config import find_tausik_dir` here closes a
    cycle through default_gates, and both call sites swallow it, leaving the
    registry silently collapsed to the universal gates."""
    source = (Path(paths.__file__)).read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.strip().startswith("#"))

    assert "import project_config" not in code
    assert "from project_config" not in code
    assert os.path.basename(paths.__file__) == "stack_registry_paths.py"
