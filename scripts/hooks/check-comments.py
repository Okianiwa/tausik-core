#!/usr/bin/env python3
"""PreToolUse hook: warns about AI-tutorial multi-line comments.

Reads tool payload from stdin, scans new content (Edit/Write) of code files,
flags 3+ consecutive `//` lines or `/* ... */` blocks with 3+ content lines.
Warn-mode: never blocks. Emits hookSpecificOutput.additionalContext on offense.
"""

import json
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")

CODE_EXTS = {".php", ".js", ".ts", ".tsx", ".jsx", ".py"}

ALLOWED_KEYWORDS = (
    "SECURITY:",
    "security:",
    "translators:",
    "phpcs:",
    "TODO",
    "FIXME",
    "XXX",
    "NOTE:",
    "eslint-",
    "prettier-",
)
DOCBLOCK_TAGS = (
    "@param",
    "@return",
    "@var",
    "@throws",
    "@see",
    "@deprecated",
    "@since",
)


def strip_comment_marker(line: str) -> str:
    s = line.strip()
    if s.startswith("//"):
        return s[2:].strip()
    s = s.lstrip("/").lstrip("*").strip()
    return s


def is_allowed_line(line: str) -> bool:
    content = strip_comment_marker(line)
    if not content:
        return True
    if any(kw in content for kw in ALLOWED_KEYWORDS):
        return True
    if content.startswith("@"):
        return True
    return False


def find_offenders(content: str):
    lines = content.splitlines()
    offenders = []

    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("//"):
            start = i
            run = []
            while i < len(lines) and lines[i].strip().startswith("//"):
                run.append(lines[i])
                i += 1
            non_allowed = [l for l in run if not is_allowed_line(l)]
            if len(run) >= 3 and len(non_allowed) >= 3:
                offenders.append(
                    {
                        "line": start + 1,
                        "kind": f"{len(run)}-line `//` block",
                        "preview": "\n".join(run[:3]),
                    }
                )
        else:
            i += 1

    block_re = re.compile(r"/\*\*?(.*?)\*/", re.DOTALL)
    for m in block_re.finditer(content):
        body = m.group(1)
        body_lines = body.splitlines()
        content_lines = []
        for bl in body_lines:
            stripped = bl.strip().lstrip("*").strip()
            if not stripped:
                continue
            if stripped.startswith(DOCBLOCK_TAGS):
                continue
            content_lines.append(stripped)
        if len(content_lines) >= 3:
            line_no = content[: m.start()].count("\n") + 1
            preview = "\n".join(m.group(0).splitlines()[:5])
            offenders.append(
                {
                    "line": line_no,
                    "kind": f"{len(content_lines)}-line docblock",
                    "preview": preview,
                }
            )

    return offenders


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool_name = payload.get("tool_name", "")
    if tool_name not in ("Edit", "Write", "MultiEdit"):
        sys.exit(0)

    tool_input = payload.get("tool_input", {}) or {}
    file_path = str(tool_input.get("file_path", ""))

    if not any(file_path.lower().endswith(ext) for ext in CODE_EXTS):
        sys.exit(0)

    new_content = tool_input.get("new_string") or tool_input.get("content") or ""
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits", []) or []
        new_content = "\n\n".join(str(e.get("new_string", "")) for e in edits)
    if not new_content or not isinstance(new_content, str):
        sys.exit(0)

    offenders = find_offenders(new_content)
    if not offenders:
        sys.exit(0)

    msg_lines = [
        f"⚠ Comment-style guard: {len(offenders)} multi-line comment(s) in {file_path}",
        "Rule (CLAUDE.md): 1-line WHY only. Multi-paragraph docblocks / 3+ consecutive `//` = drop or shrink.",
        "См. feedback_comment_style_human_terse.md для конкретных антипаттернов.",
        "",
    ]
    for off in offenders[:5]:
        msg_lines.append(f"  L{off['line']} — {off['kind']}:")
        for line in off["preview"].splitlines()[:4]:
            msg_lines.append(f"    {line}")
        msg_lines.append("")
    if len(offenders) > 5:
        msg_lines.append(f"  ... and {len(offenders) - 5} more")

    warning = "\n".join(msg_lines)

    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": warning,
            "additionalContext": warning,
        }
    }
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
