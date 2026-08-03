"""Startup smoke check for the project's MCP servers, used by `tausik doctor`.

doctor used to assert only that `server.py` EXISTS. bootstrap always puts the
file there, so the check was structurally incapable of going red on any real
failure. On 2026-07-31 that cost a full session: `mcp` 2.0.0 shipped a breaking
major (the `Server.list_tools` decorator is gone, `Server` became `MCPServer`),
a fresh bootstrap installed it, all three servers began dying on startup — and
doctor still reported "All clean". The host prints nothing but `Connection
closed`, with no traceback, so the cause was unavailable anywhere.

The check itself is cheap: launch the server with stdin closed. A healthy one
reaches the JSON-RPC loop, sees EOF and exits zero in well under a second. A
broken one dies on import or on startup and yields a traceback whose last line
is the diagnosis this whole check exists to surface.

Severity mirrors doctor's existing policy: `project` carries QG-0/QG-2, so its
death is a FAIL; the other servers degrade features without disarming the
gates, hence WARN.

Detection only — never mutates, and a bug in here must not crash doctor.
"""

from __future__ import annotations

import os
import subprocess
import sys

# A server must come up within this budget. A healthy one needs ~0.5s; the
# tenfold headroom keeps a cold import on a loaded machine from reading as a
# failure. A hung one still hits the ceiling — doctor must not wait forever.
STARTUP_TIMEOUT_SECONDS = 20

# Losing this server disarms QG-0/QG-2 over MCP; the others only narrow what
# the agent can do. Hence FAIL vs WARN.
CRITICAL_SERVER = "project"


def _venv_python(project_dir: str) -> str | None:
    """The project's venv interpreter — servers must run under it, not ours."""
    venv = os.path.join(project_dir, ".tausik", "venv")
    for rel in (os.path.join("Scripts", "python.exe"), os.path.join("bin", "python")):
        candidate = os.path.join(venv, rel)
        if os.path.isfile(candidate):
            return candidate
    return None


def discover_servers(project_dir: str) -> list[tuple[str, str]]:
    """Every `.claude/mcp/*/server.py`, sorted by name.

    Scanned rather than enumerated from a hardcoded list: a server added to the
    harness tomorrow should fall under the check by itself, with no doctor edit.
    """
    root = os.path.join(project_dir, ".claude", "mcp")
    if not os.path.isdir(root):
        return []
    found = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name, "server.py")
        if os.path.isfile(path):
            found.append((name, path))
    return found


def _diagnosis(stderr: str) -> str:
    """Last non-empty traceback line — that is where the exception type sits."""
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    return lines[-1][:160] if lines else "no stderr output"


def probe_server(
    python: str,
    server_path: str,
    project_dir: str,
    timeout: int = STARTUP_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    """Launch the server with stdin closed. Returns (came_up, explanation)."""
    try:
        r = subprocess.run(
            [python, server_path, "--project", project_dir],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"did not exit within {timeout}s — likely hung on startup"
    except OSError as e:
        return False, f"could not launch: {e}"
    if r.returncode == 0:
        return True, "starts up"
    return False, f"dies on startup (exit {r.returncode}): {_diagnosis(r.stderr)}"


def check_mcp_servers(
    project_dir: str,
    timeout: int = STARTUP_TIMEOUT_SECONDS,
) -> list[tuple[str, str, str]]:
    """(severity, label, detail) per server — same shape as doctor's other checks."""
    servers = discover_servers(project_dir)
    if not servers:
        return [("fail", "MCP servers", "no .claude/mcp/*/server.py at all — re-run bootstrap")]

    python = _venv_python(project_dir)
    if python is None:
        # doctor already FAILs on a missing venv in its own line. Don't duplicate
        # that, but don't stay silent either: the probe did not actually run.
        return [("warn", "MCP servers", "smoke skipped — no .tausik/venv")]

    results = []
    for name, path in servers:
        alive, detail = probe_server(python, path, project_dir, timeout)
        label = f"MCP server ({name})"
        if alive:
            results.append(("ok", label, detail))
        else:
            results.append(("fail" if name == CRITICAL_SERVER else "warn", label, detail))
    return results


if __name__ == "__main__":  # pragma: no cover — manual diagnostics
    for severity, label, detail in check_mcp_servers(os.getcwd()):
        print(f"{severity:<5} {label:<25} {detail}")
    sys.exit(0)
