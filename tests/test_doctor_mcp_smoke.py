"""Guard: doctor must tell a live MCP server from a dead one.

Until 2026-07-31 doctor only checked that `server.py` exists. bootstrap always
writes that file, so the check could not go red on any real failure: with all
three servers dead (an incompatible `mcp` major) doctor printed "All clean"
while the host printed nothing but `Connection closed`.

The load-bearing test here is therefore not "green on a healthy project" but
"red on a planted broken server, with the cause visible in detail". The first
without the second is exactly the check we are replacing.

Run: pytest tests/test_doctor_mcp_smoke.py -v
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SCRIPTS = os.path.join(_ROOT, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from service_doctor_mcp import (  # noqa: E402
    check_mcp_servers,
    discover_servers,
    probe_server,
)

HEALTHY = """
    import sys
    sys.stdin.read()
    sys.exit(0)
"""

# The real failure was on import/startup (`AttributeError` from the `mcp`
# major). Model that, but without depending on which `mcp` version happens to
# be installed on the machine running the tests — otherwise the broken-server
# fixture is itself broken and quietly goes green, which is the very disease
# under treatment here.
BROKEN = """
    import module_that_does_not_exist_anywhere  # noqa: F401
"""

HANGING = """
    import time
    time.sleep(60)
"""


def _make_project(tmp_path, servers: dict[str, str], with_venv: bool = True) -> str:
    """A project tree with .claude/mcp/<name>/server.py from the given sources.

    The venv holds a copy of the running interpreter: the probe only needs a
    path to something that executes Python, and a copy behaves like the real
    thing (sys.prefix still resolves to the original installation) while a
    symlink would need elevation on Windows.
    """
    for name, source in servers.items():
        d = tmp_path / ".claude" / "mcp" / name
        d.mkdir(parents=True)
        (d / "server.py").write_text(textwrap.dedent(source), encoding="utf-8")

    if with_venv:
        bindir = tmp_path / ".tausik" / "venv" / ("Scripts" if os.name == "nt" else "bin")
        bindir.mkdir(parents=True)
        shutil.copy2(sys.executable, bindir / ("python.exe" if os.name == "nt" else "python"))
    return str(tmp_path)


def test_healthy_server_reports_ok(tmp_path):
    project = _make_project(tmp_path, {"project": HEALTHY})
    results = check_mcp_servers(project, timeout=30)
    assert [(s, lbl) for s, lbl, _ in results] == [("ok", "MCP server (project)")]


def test_broken_server_fails_and_shows_cause(tmp_path):
    """Mutation: a server dying on startup must go red, and say why."""
    project = _make_project(tmp_path, {"project": BROKEN})
    severity, label, detail = check_mcp_servers(project, timeout=30)[0]
    assert severity == "fail", "a dead critical server must be FAIL"
    assert label == "MCP server (project)"
    assert "dies on startup" in detail
    assert "Error" in detail, f"no cause carried into detail: {detail}"


def test_noncritical_server_is_warn_not_fail(tmp_path):
    """brain/codebase-rag narrow features but leave the gates armed — WARN."""
    project = _make_project(tmp_path, {"brain": BROKEN})
    severity, label, _ = check_mcp_servers(project, timeout=30)[0]
    assert (severity, label) == ("warn", "MCP server (brain)")


def test_hanging_server_times_out(tmp_path):
    """Hung on startup is a failure too — and doctor must not wait for it."""
    project = _make_project(tmp_path, {"project": HANGING})
    severity, _, detail = check_mcp_servers(project, timeout=2)[0]
    assert severity == "fail"
    assert "hung" in detail


def test_missing_venv_is_warn_not_crash(tmp_path):
    project = _make_project(tmp_path, {"project": HEALTHY}, with_venv=False)
    severity, label, detail = check_mcp_servers(project)[0]
    assert (severity, label) == ("warn", "MCP servers")
    assert "venv" in detail


def test_no_servers_at_all_fails(tmp_path):
    (tmp_path / ".claude").mkdir()
    severity, label, _ = check_mcp_servers(str(tmp_path))[0]
    assert (severity, label) == ("fail", "MCP servers")


def test_discover_finds_every_server_dir(tmp_path):
    project = _make_project(
        tmp_path, {"project": HEALTHY, "brain": HEALTHY, "codebase-rag": HEALTHY}
    )
    assert [n for n, _ in discover_servers(project)] == ["brain", "codebase-rag", "project"]


def test_probe_reports_unusable_interpreter(tmp_path):
    alive, detail = probe_server("no-such-python", str(tmp_path / "s.py"), str(tmp_path))
    assert alive is False
    assert "could not launch" in detail
