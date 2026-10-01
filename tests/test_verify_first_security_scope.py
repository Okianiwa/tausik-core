"""QG-2 on a security-sensitive scope: the cache refuses its greens and
`tausik verify` mints no handle for it, so `task done` runs the verify gates
fresh inline. Before tausik-qg2-security-close such a task could not close at
all — found on mis-phpstan-zero (app/Auth/TwoFactor.php).
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from project_backend import SQLiteBackend
from project_service import ProjectService
from tausik_utils import ServiceError
from test_verify_first_contract import _stub_verify_only

_SECURE_SCOPE = ["app/Auth/TwoFactor.php"]


def _gate(passed: bool) -> list[dict]:
    return [
        {
            "name": "pytest",
            "passed": passed,
            "skipped": False,
            "severity": "block",
            "output": "ok" if passed else "1 failed",
        }
    ]


@pytest.fixture
def task_secure(tmp_path):
    svc = ProjectService(SQLiteBackend(str(tmp_path / "test.db")))
    svc.epic_add("e", "E")
    svc.story_add("e", "s", "S")
    svc.task_add("s", "t", "Implement X", goal="Implement X", role="developer")
    svc.task_update(
        "t",
        acceptance_criteria="1. X works\n2. Returns error on invalid input",
        relevant_files=json.dumps(_SECURE_SCOPE),
    )
    svc.task_start("t")
    svc.task_log("t", "AC verified: 1. X works ✓ 2. Returns error on invalid input ✓")
    return svc


@pytest.mark.verify_first
class TestSecurityScopeRunsInline:
    def test_green_inline_run_closes(self, task_secure, monkeypatch):
        _stub_verify_only(monkeypatch, auto_verify=False)
        mock_run = MagicMock(return_value=(True, _gate(True)))
        with patch.dict("sys.modules", {"gate_runner": MagicMock(run_gates=mock_run)}):
            msg = task_secure.task_done("t", ac_verified=True)
        assert "completed" in msg
        assert mock_run.called

    def test_red_inline_run_blocks(self, task_secure, monkeypatch):
        _stub_verify_only(monkeypatch, auto_verify=False)
        mock_run = MagicMock(return_value=(False, _gate(False)))
        with patch.dict("sys.modules", {"gate_runner": MagicMock(run_gates=mock_run)}):
            with pytest.raises(ServiceError):
                task_secure.task_done("t", ac_verified=True)

    def test_cached_green_is_not_trusted(self, task_secure, monkeypatch):
        sv = _stub_verify_only(monkeypatch, auto_verify=False)
        sv.record_run(
            task_secure.be._conn,
            task_slug="t",
            scope="standard",
            command=sv._build_cache_command("verify", _SECURE_SCOPE),
            exit_code=0,
            summary="pytest=PASS",
            files_hash=sv.compute_files_hash(_SECURE_SCOPE),
            duration_ms=10,
        )
        mock_run = MagicMock(return_value=(False, _gate(False)))
        with patch.dict("sys.modules", {"gate_runner": MagicMock(run_gates=mock_run)}):
            with pytest.raises(ServiceError):
                task_secure.task_done("t", ac_verified=True)
        assert mock_run.called
