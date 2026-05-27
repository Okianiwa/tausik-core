#!/usr/bin/env python3
"""Stop hook — keyword detector for agent-output drift.

Inspired by oh-my-claudecode's keyword-detector. Fires when the agent tries to
stop its turn. Reads the conversation transcript, inspects the last assistant
message for drift-announcement keywords ("I'll implement", "let me code",
"реализую это"), and if the agent is about to act without an active TAUSIK task
— blocks the stop and forces the agent to re-check task state before continuing.

Output schema for the block response (Claude Code Stop hook):
    {"decision": "block", "reason": "<instruction>"}

Always exits 0 (non-blocking outcomes are signalled via the decision field).
Skipped via TAUSIK_SKIP_HOOKS=1.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import has_active_task as _has_active_task  # noqa: E402


DRIFT_KEYWORDS = (
    # English — "I'm about to start coding" announcements
    r"\bi['’]?ll\s+(now\s+)?(implement|code|write|create|build|add|refactor|fix)\b",
    r"\blet\s+me\s+(now\s+)?(implement|code|write|create|build|add|refactor|fix|start)\b",
    r"\bi\s+will\s+(now\s+)?(implement|code|write|create|build|add|refactor|fix)\b",
    r"\bi['’]?m\s+going\s+to\s+(implement|code|write|create|build|add|refactor|fix)\b",
    r"\bgoing\s+to\s+(implement|code|write|create|build|add|refactor|fix)\s+",
    r"\bnext\s+step\s+is\s+to\s+(implement|code|write|create|build|add|refactor|fix)",
    # Russian
    r"сейчас\s+(напишу|реализую|добавлю|создам|исправлю|запилю)",
    r"приступ[аю|аем|им]\s+к\s+(реализации|написанию|добавлению|кодированию)",
    r"давайте\s+(напишем|реализуем|добавим|создадим|исправим)",
    r"я\s+(напишу|реализую|добавлю|создам|исправлю|запилю)",
)


# Search-intent patterns in the user's last prompt — when the user asks
# "where is X" / "find Y" / "how does Z work", we want the agent to reach
# for `mcp__codebase-rag__search_code` first instead of Grep/Read of full files.
SEARCH_INTENT_KEYWORDS = (
    # English
    r"\bwhere\s+is\s+\w+",
    r"\bwhere\s+(does|do)\s+\w+",
    r"\bfind\s+(the\s+)?(function|method|class|definition|implementation|usage|usages|references)\b",
    r"\bhow\s+does\s+\w+\s+(work|behave)",
    r"\bhow\s+is\s+\w+\s+(implemented|used|called)",
    r"\bwhich\s+(file|files|module|modules)\s+(define|defines|contain|contains)",
    # Russian
    r"\bгде\s+(определ|реализ|использ|объявл|задан)",
    r"\bнайди\s+(функци|метод|класс|реализаци|использовани)",
    r"\bкак\s+работает\s+\w+",
    r"\bкакие\s+файлы\s+(содерж|определ|использ)",
)


SEARCH_RECOMMENDATION = (
    "[TAUSIK rag-first nudge] Your prompt looks like a code-discovery question "
    "('where is X' / 'find Y' / 'how does Z work' / 'где определ…'). "
    "Prefer `mcp__codebase-rag__search_code` for symbol/pattern lookup — it returns "
    "ranked chunks, not full files, and is much cheaper token-wise than Grep+Read on "
    "unfamiliar code. Use Grep/Read only for known file paths."
)


# Quoted/code spans usually carry citations or symbol names, not the user's own
# intent. Stripping them before matching kills self-referential FPs where the
# skill body or the user echoes the hook's own examples (e.g. 'where is X used').
_QUOTED_SPAN = re.compile(r"'[^']*'|\"[^\"]*\"|`[^`]*`")


def _extract_text(content) -> str:
    """Normalize assistant message content to plain text.

    Claude transcripts store content as either a string or a list of blocks,
    each block being {type, text, ...}. We concatenate all text blocks.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text") or block.get("content")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def _is_tool_result_only(content) -> bool:
    """True if content is a list of blocks where every block is a tool_result.

    Claude transcripts represent tool outputs as role=user messages whose
    content is `[{"type": "tool_result", ...}, ...]`. Treating those as the
    "last user message" produced a false-positive search-intent loop in
    v14b-defect-keyword-detector-search-loop: a tool result containing the
    string "where is X" anywhere in its output triggered the rag-first nudge
    on every Stop until the agent defensively echoed `search_code`.
    """
    if not isinstance(content, list) or not content:
        return False
    return all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def _read_last_message(transcript_path: str, target_role: str) -> str:
    """Walk the transcript backwards and return the text of the most recent message
    matching `target_role` ("user" or "assistant"). Empty string if not found.

    For target_role="user", tool-result-only messages are skipped — those are
    Claude transcript wrappers for tool outputs, not actual human prompts.
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return ""
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        role = entry.get("role") or entry.get("type")
        message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        if role != target_role:
            nested_role = message.get("role") if message else None
            if nested_role != target_role:
                continue
            content = message.get("content")
        else:
            content = entry.get("content") or message.get("content")
        if target_role == "user" and _is_tool_result_only(content):
            continue
        return _extract_text(content)
    return ""


def _read_last_assistant_message(transcript_path: str) -> str:
    return _read_last_message(transcript_path, "assistant")


def _read_last_user_message(transcript_path: str) -> str:
    return _read_last_message(transcript_path, "user")


def _has_drift_keyword(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(re.search(pat, lowered) for pat in DRIFT_KEYWORDS)


def _has_search_intent(text: str) -> bool:
    if not text:
        return False
    lowered = _QUOTED_SPAN.sub(" ", text).lower()
    return any(re.search(pat, lowered) for pat in SEARCH_INTENT_KEYWORDS)


def main() -> int:
    if os.environ.get("TAUSIK_SKIP_HOOKS"):
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0

    # Anti-infinite-loop: if our previous block already injected, don't block again.
    if payload.get("stop_hook_active"):
        return 0

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    tausik_db = os.path.join(project_dir, ".tausik", "tausik.db")
    if not os.path.exists(tausik_db):
        return 0

    transcript_path = payload.get("transcript_path") or ""
    last_assistant = _read_last_assistant_message(transcript_path)

    if _has_drift_keyword(last_assistant) and not _has_active_task(project_dir):
        reason = (
            "[TAUSIK drift guard] Your last message announced code changes "
            "('I'll implement' / 'сейчас напишу' / similar) but there is no active TAUSIK task. "
            "Before proceeding: run `tausik_task_list --status active` to verify, "
            "and if no task is active, create one with `/plan` or `/go`. "
            "SENAR Rule 1 (enforced by PreToolUse) will block Write/Edit otherwise."
        )
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0

    last_user = _read_last_user_message(transcript_path)
    if _has_search_intent(last_user) and "search_code" not in last_assistant.lower():
        print(json.dumps({"decision": "block", "reason": SEARCH_RECOMMENDATION}))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
