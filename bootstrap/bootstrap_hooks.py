"""TAUSIK Claude Code hooks block builder.

Extracted from bootstrap_generate.py for filesize compliance
(v14b-filesize-debt-paydown). Public surface:

    build_hooks_dict(_hook_cmd) -> dict

Returns the same hooks block previously inlined in
generate_settings_claude. Caller passes a `_hook_cmd(script, suffix="")`
closure that knows how to format the absolute python path. The hooks
list / contract is unchanged — purely a relocation.
"""

from __future__ import annotations

from typing import Any, Callable

# How Claude Code matches a matcher against a tool name (2.1.215, function
# B8y in the bundle): a matcher built only from [a-zA-Z0-9_|] is NOT a regex
# — it is split on `|` and compared for EXACT equality. Any other character
# routes it to `new RegExp(matcher).test(name)`, an UNANCHORED search. So
# `Write|Edit` never matches `MultiEdit`, while `Write|Edit|mcp__x-y__Z`
# silently switches the whole line to substring semantics. Every matcher
# below is written anchored — `^(?:...)$` — so it means the same thing in
# either branch instead of depending on which one fired.
#
# The alias table (fSi in the bundle) maps only Task/KillShell/BashOutput and
# friends; it contains neither MultiEdit->Edit nor PowerShell->Bash, so no
# alias covers these gaps for us.

# Tools that can change a file in the repository. Guards that enforce
# "no code without a task" must see all of them. MultiEdit is kept as
# future-proofing: 2.1.215 has no such built-in tool, but the matcher
# should survive its return. NotebookEdit and the MCP file editors are
# live paths today — they were how QG-0 could be bypassed entirely.
_FILE_WRITE_TOOLS = (
    "Write|Edit|MultiEdit|NotebookEdit"
    "|mcp__windows-mcp__FileSystem"
    "|mcp__serena__(?:replace_symbol_body|replace_content"
    "|insert_after_symbol|insert_before_symbol"
    "|rename_symbol|safe_delete_symbol)"
)
FILE_WRITE_MATCHER = f"^(?:{_FILE_WRITE_TOOLS})$"

# Built-in writers only. Hooks that resolve the edited file from the payload
# (`file_path` / `notebook_path`) can act only on tools whose field name we
# know. MCP editors name that field differently per server, so matching them
# here would add coverage on paper and a silent no-op in practice.
BUILTIN_FILE_WRITE_MATCHER = "^(?:Write|Edit|MultiEdit|NotebookEdit)$"

# Tools that execute shell commands. PowerShell is the primary shell tool
# on a Windows host: under the old `Bash` matcher a push through it passed
# the push gate without a ticket, while the same push through Bash was
# blocked (observed directly in session #33). All three carry the command
# in `tool_input.command`, so the hooks parse them unchanged.
_SHELL_TOOLS = "Bash|PowerShell|mcp__windows-mcp__PowerShell"
SHELL_MATCHER = f"^(?:{_SHELL_TOOLS})$"

# Work-counting matcher for metrics: shell + file writes, no read tools.
WORK_TOOLS_MATCHER = f"^(?:{_FILE_WRITE_TOOLS}|{_SHELL_TOOLS})$"

_READ_TOOLS = "Read|Grep|Glob|WebFetch|WebSearch"

# Everything that counts as activity for the SENAR 9.2 active-time gate.
ACTIVITY_MATCHER = f"^(?:{_FILE_WRITE_TOOLS}|{_SHELL_TOOLS}|{_READ_TOOLS})$"


def build_hooks_dict(hook_cmd: Callable[..., str]) -> dict[str, Any]:
    """Build the `hooks` block of .claude/settings.json.

    `hook_cmd(script, suffix="")` returns the formatted command string
    (`python <abs path>/<script><suffix>`).
    """
    return {
        "PreToolUse": [
            {
                "matcher": FILE_WRITE_MATCHER,
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd("task_gate.py"),
                        "timeout": 10,
                    }
                ],
            },
            {
                "matcher": FILE_WRITE_MATCHER,
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd("memory_pretool_block.py"),
                        "timeout": 5,
                    }
                ],
            },
            {
                # SENAR Rule 10.12 (r14-senar-context-hygiene):
                # Warn (or block under TAUSIK_SECRET_SCAN_STRICT=1) when
                # the agent is about to write a likely secret to disk.
                "matcher": FILE_WRITE_MATCHER,
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd("secret_scan.py"),
                        "timeout": 5,
                    }
                ],
            },
            {
                "matcher": SHELL_MATCHER,
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd("bash_firewall.py"),
                        "timeout": 5,
                    }
                ],
            },
            {
                "matcher": "WebSearch|WebFetch",
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd("brain_search_proactive.py"),
                        "timeout": 5,
                    }
                ],
            },
            {
                # No `if` filter here. It used to read
                # `"if": "Bash(git push *)"` at THIS level, where Claude Code
                # never looks — the bundle reads `if` off the individual hook
                # object (`p = (k) => k.if ?? ""` applied to `k.hook`), so the
                # line was decorative and the gate has always run on every
                # shell call. Moving it into the hook object would be worse
                # than deleting it: the rule is bound to the Bash tool, so it
                # would re-open the PowerShell bypass this matcher just
                # closed. Selecting pushes is git_push_gate's own
                # _GIT_PUSH_RE, which works for any tool carrying `command`.
                "matcher": SHELL_MATCHER,
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd("git_push_gate.py"),
                        "timeout": 5,
                    }
                ],
            },
        ],
        "PostToolUse": [
            {
                "matcher": BUILTIN_FILE_WRITE_MATCHER,
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd("auto_format.py"),
                        "timeout": 15,
                    }
                ],
            },
            {
                "matcher": BUILTIN_FILE_WRITE_MATCHER,
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd("memory_posttool_audit.py"),
                        "timeout": 5,
                    }
                ],
            },
            {
                # v14b-task-done-rename-drop-v2: single tausik_task_done MCP tool.
                # Was a `tausik_task_done|tausik_task_done_v2` alternation pre-1.4
                # when both names existed; rename consolidated them.
                #
                # The shell tools are here because a task closes just as well
                # through `tausik task done <slug>` on the CLI, which is what
                # is_task_done_invocation() detects. Without them QG-2 was
                # enforced only on the MCP path — the same single-entry-point
                # assumption that let PowerShell around the push gate.
                "matcher": f"^(?:mcp__tausik-project__tausik_task_done|{_SHELL_TOOLS})$",
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd("task_done_verify.py"),
                        "timeout": 6,
                    }
                ],
            },
            {
                "matcher": "WebFetch",
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd("brain_post_webfetch.py"),
                        "timeout": 10,
                    }
                ],
            },
            {
                # HIGH-5 review fix: only writes and shell calls count
                # toward call_actual. Read/Grep/Glob are research, not work
                # — including them inflates the calibration drift metric.
                "matcher": WORK_TOOLS_MATCHER,
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd("task_call_counter.py"),
                        "timeout": 5,
                    }
                ],
            },
            {
                # v1.4 (B4): per-tool usage_events row attributed to the
                # active task — feeds `tausik metrics cost`. Wide matcher
                # because we want every tool invocation to count, not
                # just write-heavy ones. Best-effort: stays silent if
                # harness payload doesn't carry token usage.
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd("posttool_usage.py"),
                        "timeout": 4,
                    }
                ],
            },
            {
                # v1.3: writes one row per tool call to the events table
                # so backend_session_metrics.compute_active_minutes
                # actually has data to sum. Without it the 180-min SENAR
                # Rule 9.2 active-time gate never trips on read-heavy
                # work. Wide matcher (covers Read/Grep/Glob too).
                "matcher": ACTIVITY_MATCHER,
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd("activity_event.py"),
                        "timeout": 2,
                    }
                ],
            },
            {
                # v14b-start-lite-tool-truncation: coaching nudge when a
                # tool's textual output exceeds the configured threshold
                # (default 250 lines, override in
                # .tausik/config.json::tool_output_truncation_threshold).
                # Does NOT modify tool output — just emits stderr so the
                # agent reads it next turn and adjusts strategy.
                "matcher": f"^(?:Read|Grep|Glob|{_SHELL_TOOLS})$",
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd("tool_output_truncation_nudge.py"),
                        "timeout": 3,
                    }
                ],
            },
            {
                # v14c-token-budget-task: cost/token budget runaway
                # protection. Reads usage_events sum for the active task,
                # emits stderr WARN at 1.5× / BLOCKER at 2.0× of
                # cost_budget_usd or token_budget. Throttled per
                # (slug, level) via .tausik/.cost_budget_throttle.json.
                # Wide matcher (every tool call) since cost can spike on
                # any single Bash/Read; silent no-op when no active task
                # has a budget set.
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd("task_cost_budget_check.py"),
                        "timeout": 3,
                    }
                ],
            },
        ],
        "SessionStart": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd("session_start.py"),
                        "timeout": 6,
                    }
                ],
            }
        ],
        "UserPromptSubmit": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd("user_prompt_submit.py"),
                        "timeout": 5,
                    }
                ],
            }
        ],
        "Stop": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd("keyword_detector.py"),
                        "timeout": 5,
                    },
                    {
                        "type": "command",
                        "command": hook_cmd("session_cleanup_check.py"),
                        "timeout": 5,
                    },
                ],
            }
        ],
        "SessionEnd": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd("session_metrics.py", " --auto --record 2>&1 || true"),
                    }
                ],
            }
        ],
    }
