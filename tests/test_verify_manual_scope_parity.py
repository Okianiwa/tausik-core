"""fix-verify-manual-scope-mcp-cli-divergence: scope=manual asserted-green паритет CLI/service,
регресс verify-skipped-silent-nocache (memory #4), обход no-test-mapped при любом scope."""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest

_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import service_verification as sv  # noqa: E402

_DDL = """
CREATE TABLE IF NOT EXISTS verification_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_slug TEXT,
    scope TEXT NOT NULL CHECK(scope IN ('lightweight','standard','high','critical','manual')),
    command TEXT NOT NULL, exit_code INTEGER NOT NULL, summary TEXT,
    files_hash TEXT NOT NULL, ran_at TEXT NOT NULL, duration_ms INTEGER);
"""


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_DDL)
    yield c
    c.close()


def _mock_run_gates(monkeypatch, *, skipped: bool):
    """Подменяем gate_runner.run_gates: PASS, единственный pytest-гейт skipped/real."""
    import gate_runner

    monkeypatch.setattr(
        gate_runner,
        "run_gates",
        lambda trigger, files, **kw: (
            True,
            [{"name": "pytest", "passed": True, "skipped": skipped, "severity": "block"}],
        ),
    )


def _rows(conn, slug):
    return conn.execute("SELECT * FROM verification_runs WHERE task_slug=?", (slug,)).fetchall()


def test_manual_records_asserted_green_when_no_files(conn, monkeypatch):
    """AC#1: scope=manual + all-skipped + no-files → asserted-green ЗАПИСАН (паритет с CLI)."""
    _mock_run_gates(monkeypatch, skipped=True)
    passed, _results, _status = sv.run_gates_with_cache(
        conn, "t-manual", None, scope="manual", trigger="verify"
    )
    assert passed is True
    assert len(_rows(conn, "t-manual")) == 1, "manual+all-skipped+no-files → asserted-green записан"


def test_standard_does_not_record_all_skipped(conn, monkeypatch):
    """AC#4: scope=standard + all-skipped → НЕ green (регресс verify-skipped-silent-nocache #4)."""
    _mock_run_gates(monkeypatch, skipped=True)
    sv.run_gates_with_cache(conn, "t-std", None, scope="standard", trigger="verify")
    assert len(_rows(conn, "t-std")) == 0, (
        "standard+all-skipped → НЕ записан (регресс #4 не откачен)"
    )


def test_files_zero_tests_fails_even_under_manual(conn, monkeypatch):
    """AC#6а: files заявлены + мапятся в ноль тестов → ОБХОД → FAIL при ЛЮБОМ scope, включая manual."""
    _mock_run_gates(monkeypatch, skipped=True)
    passed, _results, status = sv.run_gates_with_cache(
        conn, "t-bypass", ["src/x.py"], scope="manual", trigger="verify"
    )
    assert passed is False and status == "no-test-mapped"
    assert len(_rows(conn, "t-bypass")) == 0, (
        "files+all-skipped → FAIL, не записан (обход, не эскейп)"
    )


def test_real_pass_records_regardless_of_scope(conn, monkeypatch):
    """Базовый: реальный (не-skipped) pass записывается как обычно (scope=standard)."""
    _mock_run_gates(monkeypatch, skipped=False)
    passed, _results, _status = sv.run_gates_with_cache(
        conn, "t-real", ["src/x.py"], scope="standard", trigger="verify"
    )
    assert passed is True
    assert len(_rows(conn, "t-real")) == 1, "real pass → записан (стандартный green)"
