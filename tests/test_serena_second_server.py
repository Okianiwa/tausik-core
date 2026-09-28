"""A second serena server (`serena-<name>`, bound to another tree) is guarded like the first.

One serena server serves one project, so a session that also works on a sibling
repository runs `serena-<name>` with `--project <path>`. Its editors write files
as the first one's do; before this the hooks knew only the literal
`mcp__serena__*` names and resolved every `relative_path` against the session's
project — the matcher never fired, the secret scan never looked, and a path of
the other tree was checked as if it lay in this one.

Run: pytest tests/test_serena_second_server.py -v
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

import pytest

_hooks_dir = os.path.join(os.path.dirname(__file__), "..", "scripts", "hooks")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bootstrap"))
sys.path.insert(0, _hooks_dir)

from _common import edited_file_paths, is_file_write_tool, serena_project_root  # noqa: E402
from bootstrap_hooks import FILE_WRITE_MATCHER, WORK_TOOLS_MATCHER  # noqa: E402
from test_hook_tool_coverage import matcher_matches  # noqa: E402

_TASK_GATE = os.path.join(_hooks_dir, "task_gate.py")
_SECRET_SCAN = os.path.join(_hooks_dir, "secret_scan.py")
_AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"


def _project(tmp_path, other_root, *, active_task: bool = False):
    proj = tmp_path / "proj"
    (proj / ".tausik").mkdir(parents=True)
    conn = sqlite3.connect(str(proj / ".tausik" / "tausik.db"))
    conn.execute("CREATE TABLE tasks (slug TEXT PRIMARY KEY, status TEXT)")
    conn.execute("INSERT INTO tasks VALUES ('t', ?)", ("active" if active_task else "planning",))
    conn.commit()
    conn.close()
    (proj / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "serena-mis": {
                        "command": "serena.exe",
                        "args": [
                            "start-mcp-server",
                            "--context",
                            "claude-code",
                            "--project",
                            str(other_root),
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return proj


def _run(hook, payload, project_dir, **env_extra):
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir), **env_extra}
    for key in ("TAUSIK_SKIP_HOOKS", "TAUSIK_HOOK_FAIL_OPEN"):
        if key not in env_extra:
            env.pop(key, None)
    return subprocess.run(
        [sys.executable, hook],
        input=json.dumps(payload),
        text=True,
        encoding="utf-8",
        capture_output=True,
        env=env,
        timeout=10,
    )


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        ("mcp__serena__replace_content", True),
        ("mcp__serena-mis__replace_content", True),
        ("mcp__serena-mis__safe_delete_symbol", True),
        ("mcp__serena-mis__find_symbol", False),
        ("mcp__serenade__replace_content", False),
        ("mcp__other__replace_content", False),
        ("mcp__serena-mis__replace_content__x", False),
    ],
)
def test_write_tool_recognition(tool, expected):
    assert is_file_write_tool(tool) is expected


@pytest.mark.parametrize("matcher", [FILE_WRITE_MATCHER, WORK_TOOLS_MATCHER])
def test_matchers_see_the_second_server_and_nothing_wider(matcher):
    assert matcher_matches("mcp__serena-mis__replace_content", matcher)
    assert matcher_matches("mcp__serena-mis__rename_symbol", matcher)
    assert not matcher_matches("mcp__serena-mis__find_symbol", matcher)
    assert not matcher_matches("mcp__serenade__replace_content", matcher)


def test_root_comes_from_the_declared_project(tmp_path):
    other = tmp_path / "other"
    proj = _project(tmp_path, other)
    assert serena_project_root("mcp__serena__replace_content", str(proj)) == str(proj)
    assert serena_project_root("mcp__serena-mis__replace_content", str(proj)) == str(other)
    assert serena_project_root("mcp__serena-ghost__replace_content", str(proj)) is None
    assert serena_project_root("Edit", str(proj)) is None


def test_relative_path_resolves_against_the_serving_tree(tmp_path, monkeypatch):
    other = tmp_path / "other"
    proj = _project(tmp_path, other)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    rel = {"relative_path": "app/Mod.php"}
    assert edited_file_paths(rel, "mcp__serena-mis__replace_content") == [
        os.path.normpath(str(other / "app/Mod.php"))
    ]
    assert edited_file_paths(rel, "mcp__serena__replace_content") == [
        os.path.normpath(str(proj / "app/Mod.php"))
    ]
    # undeclared server: stays in the session's project, so it stays gated
    assert edited_file_paths(rel, "mcp__serena-ghost__replace_content") == [
        os.path.normpath(str(proj / "app/Mod.php"))
    ]


def test_task_gate_judges_the_second_tree_as_the_other_repository(tmp_path):
    other = tmp_path / "other"
    proj = _project(tmp_path, other)
    call = {"tool_input": {"relative_path": "app/Mod.php", "needle": "a", "repl": "b"}}
    # the sibling tree is outside this project: the same answer Edit with its absolute path gets
    assert (
        _run(_TASK_GATE, {"tool_name": "mcp__serena-mis__replace_content", **call}, proj).returncode
        == 0
    )
    # without a task, a path of THIS project stays blocked through either server
    assert (
        _run(_TASK_GATE, {"tool_name": "mcp__serena__replace_content", **call}, proj).returncode
        == 2
    )
    assert (
        _run(
            _TASK_GATE, {"tool_name": "mcp__serena-ghost__replace_content", **call}, proj
        ).returncode
        == 2
    )
    inside = {
        "tool_input": {"relative_path": str(proj / "app/Mod.php"), "needle": "a", "repl": "b"}
    }
    assert (
        _run(
            _TASK_GATE, {"tool_name": "mcp__serena-mis__replace_content", **inside}, proj
        ).returncode
        == 2
    )


def test_secret_scan_reads_the_second_servers_edits(tmp_path):
    proj = _project(tmp_path, tmp_path / "other")
    edit = {"tool_input": {"relative_path": "a.php", "needle": "x", "repl": f"key = '{_AWS_KEY}'"}}
    strict = {"TAUSIK_SECRET_SCAN_STRICT": "1"}
    assert (
        _run(
            _SECRET_SCAN, {"tool_name": "mcp__serena-mis__replace_content", **edit}, proj, **strict
        ).returncode
        == 2
    )
    assert (
        _run(
            _SECRET_SCAN, {"tool_name": "mcp__serenade__replace_content", **edit}, proj, **strict
        ).returncode
        == 0
    )
