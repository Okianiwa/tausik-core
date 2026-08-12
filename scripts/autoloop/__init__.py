"""Autonomous execution loop for this project.

Not part of the TAUSIK library: `.tausik-lib/` is a git checkout of the
upstream fork and any edit there is lost on the next `/fab update`. This
package is project-owned and survives library refresh.

Modules:
    context     — read current context-window fill from a transcript
    state       — .autoloop.json read/write + task completion state
    sensor      — Stop hook: measures and records, never blocks
"""
