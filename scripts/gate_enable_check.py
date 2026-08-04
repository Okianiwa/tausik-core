"""Decide whether `tausik gates enable <name>` can produce a working gate.

Why this exists: `gate_enable` used to write `{"enabled": true}` into
config.json for ANY name and report "Gate 'X' enabled." Measured on `tsc` in a
python project — the stored entry carried no command, no trigger and no
severity, and `get_gates_for_trigger` returned it on none of commit/task-done/
verify/review. The user enabled a gate, `gates status` printed `[ON]`, and
nothing ever ran (task gates-registry-split-cli-vs-hook).

Deliberately NOT folded into `project_config._validate_custom_gate`: that one
answers a security question ("may this command be executed?"), for which "no
command" is correctly a pass — built-ins like `filesize` carry none. This
answers a different question ("will enabling this name actually gate
anything?") and needs the registry, not the shell rules.
"""

from __future__ import annotations

from typing import Any

# Gates dispatched by name inside gate_runner rather than by a shell command.
# Enabling one of these without a `command` is legitimate.
BUILTIN_DISPATCH_GATES = frozenset({"filesize", "tdd_order", "bootstrap_drift"})


def _stacks_owning(gate_name: str, catalog: Any) -> list[str]:
    """Catalog stacks whose stack.json declares `gate_name`."""
    owners = []
    for stack in sorted(catalog.all_stacks()):
        if gate_name in catalog.gates_for(stack):
            owners.append(stack)
    return owners


def check_gate_enable(
    name: str,
    active_gates: dict[str, dict],
    user_gates: dict[str, dict],
    catalog: Any,
) -> str | None:
    """None when enabling `name` yields a real gate, else the refusal text.

    `active_gates` is the project's resolved registry (DEFAULT_GATES),
    `user_gates` the `gates` section of config.json, `catalog` a registry over
    every shipped stack.
    """
    if name in active_gates:
        return None

    # A custom gate the user fully described is legitimate even though it is
    # absent from the registry — that is the documented extension point.
    existing = user_gates.get(name) or {}
    if existing.get("command") or name in BUILTIN_DISPATCH_GATES:
        return None

    owners = _stacks_owning(name, catalog)
    if owners:
        # Both remedies were verified by executing them, not by reading them:
        # the first draft advised adding the stack to `bootstrap.stacks`, which
        # does nothing — bootstrap.py:213 overwrites that key from detection on
        # every run, so the retry failed identically.
        return (
            f"Gate '{name}' belongs to stack(s) {', '.join(owners)}, which this "
            f"project does not deploy — enabling it would store a dead entry "
            f"that never runs.\n"
            f"Fix, either:\n"
            f"  1. make the stack detectable: add its marker file (tsconfig.json "
            f"for typescript, go.mod for go, ...), re-run "
            f"`python bootstrap/bootstrap.py`, then enable again. Hand-editing "
            f'"bootstrap": {{"stacks": [...]}} does NOT work — bootstrap '
            f"rewrites it from detection; or\n"
            f"  2. define it as a custom gate: give '{name}' a \"command\" "
            f'(and "trigger") under "gates" in .tausik/config.json.'
        )

    return (
        f"Unknown gate '{name}' — not in this project's registry and not a "
        f"stack gate TAUSIK ships.\n"
        f"Available here: {', '.join(sorted(active_gates))}.\n"
        f'Fix: to add your own, give it a "command" (and "trigger") under '
        f'"gates" in .tausik/config.json, then enable it.'
    )
