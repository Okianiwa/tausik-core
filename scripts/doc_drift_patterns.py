"""Which files are scanned for stale constants, and the shapes we look for.

Split out of `gen_doc_constants` when that module crossed the 400-line filesize
gate. Only declarations live here — the scanners and the generator stay put, so
`gen_doc_constants` re-exports every name below and existing imports keep
working.

Patterns are deliberately narrow. A loose one turns illustrative prose ("never
add 5 tests where one parametrized test covers it") into a drift report, and a
checker that cries wolf gets muted, which costs more than the drift it catches.
"""

from __future__ import annotations

import re

_VERSION_RE = re.compile(r"\bv(\d+)\.(\d+)(?:\.(\d+))?(?:\.x)?\b")
_FENCED_BLOCK_RE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)

CROSS_FILE_SCAN_TARGETS: tuple[str, ...] = (
    "README.md",
    "README.ru.md",
    "AGENTS.md",
    "CLAUDE.md",
    "docs/en/architecture.md",
    "docs/ru/architecture.md",
    "docs/en/mcp.md",
    "docs/ru/mcp.md",
)

# Products with their own version timelines — a `vX.Y` next to one of these is
# not TAUSIK's version and must not be judged against it.
_FOREIGN_VERSION_PREFIXES: tuple[str, ...] = ("SENAR", "Python", "OWASP")

# RU/EN word for "tool" in MCP-count contexts. Matches singular + plural genitive
# forms: tools, tool, инструмент, инструмента, инструментов.
_TOOL_WORD = r"(?:tools?|инструмент(?:а|ов)?)"

# MCP tool-count patterns. Each entry is (compiled regex, constants_key, label).
# The capture group is a single integer compared against constants.json[key].
# Patterns are ordered specific-first so context-rich matches (brain header)
# fire before generic ones (`X project tools`).
_MCP_COUNT_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    # `tausik-brain`, N tools — brain server header, e.g. "## Shared Brain (`tausik-brain`, 7 tools)"
    (
        re.compile(rf"`tausik-brain`[^)]*?,\s*(\d+)\s+{_TOOL_WORD}", re.IGNORECASE),
        "mcp_brain_tools",
        "tausik-brain server header",
    ),
    # **N tools** / **N MCP tools** / **N MCP-инструментов** — markdown bold main count
    (
        re.compile(rf"\*\*(\d+)\s+(?:MCP[-\s]+)?{_TOOL_WORD}\*\*", re.IGNORECASE),
        "mcp_main_tools",
        "main count (bold)",
    ),
    # N project tools — explicit project count, e.g. "93 project tools"
    (
        re.compile(rf"\b(\d+)\s+project\s+{_TOOL_WORD}\b", re.IGNORECASE),
        "mcp_project_tools",
        "project count",
    ),
    # N brain tools — explicit brain count, e.g. "7 brain tools"
    (
        re.compile(rf"\b(\d+)\s+brain\s+{_TOOL_WORD}\b", re.IGNORECASE),
        "mcp_brain_tools",
        "brain count",
    ),
)

# Pair pattern: "(N project + M brain ...)" — both groups checked independently.
_MCP_COUNT_PAIR_PATTERN: tuple[re.Pattern[str], tuple[str, str], str] = (
    re.compile(r"\((\d+)\s+project\s*\+\s*(\d+)\s+brain", re.IGNORECASE),
    ("mcp_project_tools", "mcp_brain_tools"),
    "project+brain pair",
)

# Test-count patterns. Each entry is (compiled regex, label). The capture
# group is a single integer compared against constants.json["test_count"].
_TEST_COUNT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # "pytest suite (N tests)"
    (re.compile(r"pytest\s+suite\s+\((\d+)\s+tests?\)", re.IGNORECASE), "pytest suite count"),
    # Badge URL: "tests-2590%20passed-brightgreen"
    (re.compile(r"tests-(\d+)%20passed", re.IGNORECASE), "badge URL count"),
    # Badge alt-text: "[![2590 tests](...)]"
    (re.compile(r"!\[(\d+)\s+tests?\]"), "badge label count"),
    # Markdown bold: "**N tests**" (used in changelogs / release notes)
    (re.compile(r"\*\*(\d+)\s+tests?\*\*"), "bold tests count"),
    # Prose: "covered by N tests" (README's v1.4 notice). Added after that
    # sentence was found holding 3378 while the badge two lines above said 3583
    # and the live count was 3610 — three numbers in one file, nothing checking
    # the third. `[\s>]+` rather than `\s+`: the sentence wraps mid-phrase
    # inside a blockquote, so the literal text is "covered\n> by 3378 tests".
    # Anchored on "covered by" so prose like "fixed by 2 tests" stays out.
    (re.compile(r"covered[\s>]+by\s+(\d+)\s+tests?", re.IGNORECASE), "prose coverage count"),
)
