"""Guard x tool coverage: every tool a guard's action is reachable with.

A guard is only real if the call clears BOTH filters it passes through:

  1. the ``matcher`` in .claude/settings.json — this file ports Claude Code's
     matching algorithm so the assertions test real coverage rather than the
     presence of a substring;
  2. the ``tool_name`` set inside the hook itself.

When the two drift the hook still runs and still exits 0 — a pass that is
indistinguishable from never having looked. That is the defect this file
exists to catch, so the table below is deliberately explicit about what must
NOT match as well as what must.

Run: pytest tests/test_hook_tool_coverage.py -v
"""

from __future__ import annotations

import os
import re
import sys

import pytest


_bootstrap_dir = os.path.join(os.path.dirname(__file__), "..", "bootstrap")
_hooks_dir = os.path.join(os.path.dirname(__file__), "..", "scripts", "hooks")
sys.path.insert(0, _bootstrap_dir)
sys.path.insert(0, _hooks_dir)

from _common import (  # noqa: E402
    BUILTIN_FILE_WRITE_TOOL_NAMES,
    FILE_WRITE_TOOL_NAMES,
    SHELL_TOOL_NAMES,
    edited_file_path,
    edited_file_paths,
    is_task_done_invocation,
)
from bootstrap_hooks import (  # noqa: E402
    ACTIVITY_MATCHER,
    BUILTIN_FILE_WRITE_MATCHER,
    FILE_WRITE_MATCHER,
    SHELL_MATCHER,
    WORK_TOOLS_MATCHER,
    build_hooks_dict,
)


# Port of the bundle's B8y(e, t, r, n) — Claude Code 2.1.215. The branch that
# trips people up is the first one: a matcher built only from [a-zA-Z0-9_|] is
# NOT compiled as a regex, it is split on `|` and compared for exact equality.
# That is why `Write|Edit` never matched `MultiEdit`. The bundle warns about it
# too: "Hook matcher `X` matches no tool (it is compared as an exact string)".
_ALIASES = {
    "Task": "Agent",
    "KillShell": "TaskStop",
    "KillBash": "TaskStop",
    "AgentOutputTool": "TaskOutput",
    "BashOutputTool": "TaskOutput",
    "AgentOutput": "TaskOutput",
    "BashOutput": "TaskOutput",
    "ListPeers": "ListAgents",
    "Brief": "SendUserMessage",
    "ListMcpResources": "ListMcpResourcesTool",
    "ReadMcpResource": "ReadMcpResourceTool",
    "ReadMcpResourceDir": "ReadMcpResourceDirTool",
}


def matcher_matches(tool_name: str, matcher: str, allow_comma: bool = False) -> bool:
    if not matcher or matcher == "*":
        return True
    simple = r"[a-zA-Z0-9_|, -]+" if allow_comma else r"[a-zA-Z0-9_|]+"
    if re.fullmatch(simple, matcher):
        parts = re.split(r"[|,]" if allow_comma else r"\|", matcher)
        return tool_name in [_ALIASES.get(p.strip(), p.strip()) for p in parts if p.strip()]
    try:
        return re.search(matcher, tool_name) is not None
    except re.error:
        return False


def test_port_reproduces_the_exact_string_branch():
    """The port itself must show the trap, or it proves nothing downstream."""
    assert matcher_matches("Write", "Write|Edit")
    assert not matcher_matches("MultiEdit", "Write|Edit")
    assert not matcher_matches("NotebookEdit", "Write|Edit")
    assert not matcher_matches("PowerShell", "Bash")
    # Anchored form means the same thing in either branch.
    assert matcher_matches("MultiEdit", "^(?:Write|Edit|MultiEdit)$")
    assert not matcher_matches("MultiEditX", "^(?:Write|Edit|MultiEdit)$")
    # The other half of the trap: one name containing a character outside
    # [a-zA-Z0-9_|] — a hyphen in any mcp__server__tool name is enough —
    # flips the WHOLE matcher to an unanchored substring search.
    assert not matcher_matches("NotebookEdit", "Edit|Write")
    assert matcher_matches("NotebookEdit", "Edit|Write|mcp__windows-mcp__FileSystem")


# The write tools, written out once, independently of both constants under
# test. Without this the coverage assertions are circular: _MUST_MATCH is
# derived from FILE_WRITE_TOOL_NAMES, so deleting a name from the frozenset
# AND the matcher together left the suite green — verified by mutation.
# Adding a genuinely new write tool is supposed to fail here first; that
# failure is the reminder to check layer 3 (its path field) too.
_EXPECTED_FILE_WRITE_TOOLS = {
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    "mcp__windows-mcp__FileSystem",
    "mcp__serena__replace_symbol_body",
    "mcp__serena__replace_content",
    "mcp__serena__insert_after_symbol",
    "mcp__serena__insert_before_symbol",
    "mcp__serena__rename_symbol",
    "mcp__serena__safe_delete_symbol",
}

_EXPECTED_SHELL_TOOLS = {"Bash", "PowerShell", "mcp__windows-mcp__PowerShell"}


def test_tool_sets_match_the_independent_expectation():
    assert set(FILE_WRITE_TOOL_NAMES) == _EXPECTED_FILE_WRITE_TOOLS
    assert set(SHELL_TOOL_NAMES) == _EXPECTED_SHELL_TOOLS
    assert set(BUILTIN_FILE_WRITE_TOOL_NAMES) == {
        "Write",
        "Edit",
        "MultiEdit",
        "NotebookEdit",
    }


# Tools that must reach each guard, and tools that must NOT. The negative
# column is what keeps a matcher of `.*` from passing this file.
_MUST_MATCH = {
    "task_gate.py": _EXPECTED_FILE_WRITE_TOOLS,
    "memory_pretool_block.py": _EXPECTED_FILE_WRITE_TOOLS,
    "secret_scan.py": _EXPECTED_FILE_WRITE_TOOLS,
    "bash_firewall.py": _EXPECTED_SHELL_TOOLS,
    "git_push_gate.py": _EXPECTED_SHELL_TOOLS,
    "task_done_verify.py": _EXPECTED_SHELL_TOOLS,
    "auto_format.py": {"Write", "Edit", "MultiEdit", "NotebookEdit"},
    "memory_posttool_audit.py": {"Write", "Edit", "MultiEdit", "NotebookEdit"},
}

_MUST_NOT_MATCH = {
    "task_gate.py": {"Read", "Grep", "Glob", "Bash", "PowerShell"},
    "memory_pretool_block.py": {"Read", "Grep", "Bash"},
    "secret_scan.py": {"Read", "Grep", "Bash"},
    "bash_firewall.py": {"Read", "Write", "Edit"},
    "git_push_gate.py": {"Read", "Write", "Edit"},
    "auto_format.py": {"Read", "Bash", "PowerShell"},
    "memory_posttool_audit.py": {"Read", "Bash", "PowerShell"},
}


def _matchers_by_hook() -> dict[str, str]:
    """Read matchers off the generator, not off generated settings.json.

    .claude/ is a build artifact; asserting against it would let a stale
    deploy pass while the source stayed broken.
    """
    hooks = build_hooks_dict(lambda script, suffix="": f"python /abs/{script}{suffix}")
    out: dict[str, str] = {}
    for groups in hooks.values():
        for group in groups:
            for hook in group.get("hooks", []):
                script = hook.get("command", "").rsplit("/", 1)[-1].split(" ")[0]
                out.setdefault(script, group.get("matcher", ""))
    return out


@pytest.mark.parametrize("script", sorted(_MUST_MATCH))
def test_guard_matcher_covers_every_tool_that_can_perform_the_action(script):
    matcher = _matchers_by_hook().get(script)
    assert matcher is not None, f"{script} is not wired into build_hooks_dict"
    for tool in sorted(_MUST_MATCH[script]):
        assert matcher_matches(tool, matcher), (
            f"{script}: matcher {matcher!r} does not see {tool!r} — "
            "the guard will never run for that tool"
        )


@pytest.mark.parametrize("script", sorted(_MUST_NOT_MATCH))
def test_guard_matcher_stays_narrow(script):
    matcher = _matchers_by_hook()[script]
    for tool in sorted(_MUST_NOT_MATCH[script]):
        assert not matcher_matches(tool, matcher), (
            f"{script}: matcher {matcher!r} unexpectedly matches {tool!r}"
        )


def test_matcher_and_in_hook_tool_set_agree():
    """Layer 1 and layer 2 must cover the same names.

    Either one silently narrower than the other yields a hook that runs and
    exits 0 without inspecting anything.
    """
    for tool in FILE_WRITE_TOOL_NAMES:
        assert matcher_matches(tool, FILE_WRITE_MATCHER), tool
    for tool in BUILTIN_FILE_WRITE_TOOL_NAMES:
        assert matcher_matches(tool, BUILTIN_FILE_WRITE_MATCHER), tool
    for tool in SHELL_TOOL_NAMES:
        assert matcher_matches(tool, SHELL_MATCHER), tool
    for tool in FILE_WRITE_TOOL_NAMES | SHELL_TOOL_NAMES:
        assert matcher_matches(tool, WORK_TOOLS_MATCHER), tool
        assert matcher_matches(tool, ACTIVITY_MATCHER), tool


def test_multiedit_and_notebookedit_reach_the_task_gate():
    """The regression this task was filed for, stated in one place.

    MultiEdit has no built-in tool in 2.1.215 and is covered as
    future-proofing; NotebookEdit is a live write path today.
    """
    matcher = _matchers_by_hook()["task_gate.py"]
    assert matcher_matches("MultiEdit", matcher)
    assert matcher_matches("NotebookEdit", matcher)
    assert "MultiEdit" in FILE_WRITE_TOOL_NAMES
    assert "NotebookEdit" in FILE_WRITE_TOOL_NAMES


def test_push_gate_covers_powershell():
    """Session #33 observed a push through PowerShell passing without a ticket."""
    matcher = _matchers_by_hook()["git_push_gate.py"]
    for tool in ("Bash", "PowerShell", "mcp__windows-mcp__PowerShell"):
        assert matcher_matches(tool, matcher), tool


def test_push_gate_group_carries_no_if_field():
    """`if` belongs on the hook object; on the group Claude Code never reads it.

    Restoring it here would be worse than the deletion: the rule is bound to
    the Bash tool, so it would re-open the PowerShell bypass.
    """
    hooks = build_hooks_dict(lambda script, suffix="": f"python /abs/{script}{suffix}")
    for groups in hooks.values():
        for group in groups:
            assert "if" not in group, f"stray `if` on matcher group {group.get('matcher')!r}"


def test_task_done_detected_through_any_shell():
    """QG-2 was bypassable by closing a task from PowerShell instead of Bash."""
    cmd = {"command": ".tausik/tausik task done some-slug"}
    for tool in SHELL_TOOL_NAMES:
        assert is_task_done_invocation(tool, cmd), tool
    assert not is_task_done_invocation("Read", cmd)


def test_edited_file_path_reads_each_tools_field(tmp_path, monkeypatch):
    """Layer 3 — keyed to the real tool schemas.

    A name in the matcher whose path field the hook cannot read is coverage
    on paper only: the guard matches, finds nothing, and exits 0.
    """
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    # normpath because the hooks normalise separators — compare the same way
    # rather than assuming POSIX slashes on a Windows host.
    assert edited_file_path({"file_path": "/abs/a.py"}) == os.path.normpath("/abs/a.py")
    assert edited_file_path({"notebook_path": "/abs/b.ipynb"}) == os.path.normpath("/abs/b.ipynb")
    assert edited_file_path({"path": "/abs/c.txt"}) == os.path.normpath("/abs/c.txt")
    # serena speaks relative_path, resolved against the project dir.
    resolved = edited_file_path({"relative_path": "pkg/mod.py"})
    assert resolved == os.path.normpath(os.path.join(str(tmp_path), "pkg/mod.py"))
    assert os.path.isabs(resolved)
    assert edited_file_path({}) == ""
    assert edited_file_path({"file_path": ""}) == ""
    assert edited_file_path("not a dict") == ""


def test_every_tool_in_the_write_set_has_a_readable_path_field():
    """Each write tool must expose its target through a field we parse."""
    samples = {
        "Write": {"file_path": "/x/a.py"},
        "Edit": {"file_path": "/x/a.py"},
        "MultiEdit": {"file_path": "/x/a.py"},
        "NotebookEdit": {"notebook_path": "/x/a.ipynb"},
        "mcp__windows-mcp__FileSystem": {"mode": "write", "path": "/x/a.py"},
        "mcp__serena__replace_symbol_body": {"relative_path": "a.py"},
        "mcp__serena__replace_content": {"relative_path": "a.py"},
        "mcp__serena__insert_after_symbol": {"relative_path": "a.py"},
        "mcp__serena__insert_before_symbol": {"relative_path": "a.py"},
        "mcp__serena__rename_symbol": {"relative_path": "a.py"},
        "mcp__serena__safe_delete_symbol": {"relative_path": "a.py"},
    }
    assert set(samples) == _EXPECTED_FILE_WRITE_TOOLS, "new write tool: add a sample"
    for tool, payload in samples.items():
        assert edited_file_paths(payload), f"{tool}: no path field the hooks can read"


def test_relative_path_cannot_climb_out_unnoticed(tmp_path, monkeypatch):
    """`..` must be collapsed, or an escaping path never equals the guarded one."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))
    escaped = edited_file_path({"relative_path": "../outside/secrets.md"})
    assert ".." not in escaped
    assert escaped == os.path.normpath(str(tmp_path / "outside" / "secrets.md"))


def test_filesystem_move_exposes_the_destination():
    """move/copy write to `destination`; checking only `path` inspects the source."""
    paths = edited_file_paths(
        {"mode": "move", "path": "/tmp/staged.md", "destination": "/guarded/target.md"}
    )
    assert os.path.normpath("/guarded/target.md") in paths


def test_db_timeout_budget_fits_the_hook_timeout():
    """A hook killed on timeout is an ALLOW, so the SELECTs must finish first.

    SQLite's busy handler overshoots its nominal timeout (measured ~1.5x), and
    an earlier 2.0 + 5.0 pair took 10.65s against a 10s budget — losing the
    block in 3 runs out of 3, exactly inverting the fail-secure guarantee.
    """
    import task_gate

    configured = None
    hooks = build_hooks_dict(lambda script, suffix="": f"python /abs/{script}{suffix}")
    for groups in hooks.values():
        for group in groups:
            for hook in group.get("hooks", []):
                if "task_gate.py" in hook.get("command", ""):
                    configured = hook.get("timeout")
    assert configured, "task_gate.py has no timeout configured"

    worst_case = sum(task_gate._DB_TIMEOUTS) * task_gate._SQLITE_OVERSHOOT
    startup_allowance = 1.5
    assert worst_case + startup_allowance < configured, (
        f"sqlite budget {worst_case:.1f}s + startup exceeds the {configured}s "
        "hook timeout — a locked DB would silently allow the write"
    )


def test_secret_scan_fallback_matches_the_real_set():
    """The ImportError fallback must not drift from _common."""
    import secret_scan

    assert set(secret_scan.FILE_WRITE_TOOL_NAMES) == _EXPECTED_FILE_WRITE_TOOLS
