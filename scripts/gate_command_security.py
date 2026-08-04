"""Security rules for custom gate commands: what may run, and how.

Split out of `project_config.py` when that module crossed the filesize gate.
The seam is deliberate rather than arbitrary: everything here answers one
question — "may this command string be executed?" — and nothing here knows
about triggers, stacks, or the registry.

Note the boundary. A gate entry can pass every check in this module and still
gate nothing at all (no command, no trigger). Whether an entry is USEFUL is a
different question, answered by `gate_enable_check.check_gate_enable`.
"""

from __future__ import annotations

import re

ALLOWED_GATE_EXECUTABLES = frozenset(
    {
        "pytest",
        "ruff",
        "mypy",
        "bandit",
        "tsc",
        "eslint",
        "go",
        "golangci-lint",
        "cargo",
        "clippy",
        "phpstan",
        "phpcs",
        "javac",
        "ktlint",
        "npm",
        "npx",
        "pnpm",
        "yarn",
        "make",
        "python",
        "ruby",
        "php",
        # IaC tooling — added when stack-iac-vertical introduced default gates
        # (HIGH-1 review fix: without this, user overrides like
        # vendor/bin/ansible-lint silently fail validate_custom_gate).
        "ansible-lint",
        "ansible",
        "terraform",
        "tflint",
        "tofu",
        "helm",
        "kubeval",
        "kube-score",
        "hadolint",
    }
)

# Shell operators forbidden in commands that use {files} placeholder
# (broader rule because file paths are user-controlled in {files}).
_SHELL_INJECTION_PATTERN = re.compile(r"\||\&\&|\|\||;|\$\(|`")

# Shell chain/substitution operators that are NEVER acceptable in custom
# gates regardless of {files} — legitimate static gates may pipe stdout
# to head/tail (single `|`), but command chaining (&&, ||, ;) and
# command-substitution ($(, backtick) signal an attempt to escape the
# allowed-executable whitelist. HIGH-2 review fix.
_SHELL_CHAIN_PATTERN = re.compile(r"&&|\|\||;|\$\(|`")


def _validate_trigger_args(name: str, gate: dict) -> str | None:
    """Hold `trigger_args` to the same rules as the command it is glued onto.

    `run_command_gate` appends these to the command string before execution,
    so anything the command may not contain, these may not contain either —
    otherwise the allowed-executable whitelist is escaped through the back
    door by a config key nobody checked.
    """
    raw = gate.get("trigger_args")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return f"Custom gate '{name}': trigger_args must be an object mapping trigger -> arguments."
    for trigger, extra in raw.items():
        if not isinstance(extra, str):
            return f"Custom gate '{name}': trigger_args.{trigger} must be a string."
        if _SHELL_INJECTION_PATTERN.search(extra):
            return (
                f"Custom gate '{name}': trigger_args.{trigger} contains shell "
                f"operators — refused (they are appended to the command)."
            )
    return None


def validate_custom_gate(name: str, gate: dict) -> str | None:
    """Validate a custom gate command for security.

    Returns None if valid, or an error message string if invalid.
    HIGH-2 review fix: shell metachars are blocked unconditionally now —
    previously the guard required `{files}` placeholder, which let a
    custom gate run pipelines under shell=True without scrutiny.
    """
    trigger_args_error = _validate_trigger_args(name, gate)
    if trigger_args_error:
        return trigger_args_error

    command = gate.get("command")
    if not command or command is None:
        # Nothing to execute, so nothing for a SECURITY check to judge. This is
        # not a statement that the entry is useful: reached from `load_gates`,
        # the name is by definition absent from DEFAULT_GATES, so it is not
        # "a built-in like filesize" — that claim used to sit here and made the
        # pass look deliberate. See this module's docstring for the boundary.
        return None

    # Extract first token (the executable)
    first_token = command.split()[0] if command.strip() else ""
    # Strip path prefixes (e.g. "vendor/bin/phpstan" -> "phpstan")
    exe = first_token.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

    if exe not in ALLOWED_GATE_EXECUTABLES:
        return (
            f"Custom gate '{name}': executable '{exe}' not in allowed list. "
            f"Allowed: {sorted(ALLOWED_GATE_EXECUTABLES)}"
        )

    # Always reject command chaining / substitution — these escape the
    # allowed-executable whitelist regardless of placeholder usage.
    if _SHELL_CHAIN_PATTERN.search(command):
        return (
            f"Custom gate '{name}': command contains shell operators "
            f"(&&/||/;/$(/`) — refused. Use a wrapper script or split "
            f"into multiple gates."
        )

    # Stricter rule when the user-controlled {files} placeholder is in
    # play: block bare pipes too, since they let user input redirect
    # to an arbitrary downstream command.
    if "{files}" in command and _SHELL_INJECTION_PATTERN.search(command):
        return (
            f"Custom gate '{name}': command contains shell operators "
            f"with {{files}} placeholder — potential injection risk."
        )

    return None
