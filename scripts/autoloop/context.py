"""Measure how full the model's context window currently is.

The number that matters is NOT the session's cumulative token spend (that is
what `session_metrics.py` reports — it sums usage across every message). It is
the size of the prompt on the LAST request: input + cache_creation + cache_read
of the most recent assistant message. That triple is what the model actually
re-reads each turn, so it is what runs out.

Sidechain entries (subagents) carry their own window and are skipped — counting
them would report a fill the main conversation never had.
"""

from __future__ import annotations

import json
import os

# Grow the tail read until an assistant message with usage turns up. A single
# tool result can exceed 64 KB, so one small read is not enough; reading the
# whole file is not an option either (transcripts reach tens of MB).
_TAIL_STEPS_BYTES = (64 * 1024, 512 * 1024, 4 * 1024 * 1024)

DEFAULT_CONTEXT_WINDOW = 1_000_000

REASON_UNREADABLE = "transcript_unreadable"
REASON_NO_USAGE = "no_usage_found"


def _read_tail(path: str, max_bytes: int) -> str:
    """Return the last `max_bytes` of a file, dropping a partial first line."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - max_bytes))
            blob = f.read()
    except OSError:
        return ""
    if not blob:
        return ""
    text = blob.decode("utf-8", errors="replace")
    if size > max_bytes:
        newline = text.find("\n")
        text = text[newline + 1 :] if newline >= 0 else ""
    return text


def _usage_tokens(usage: dict) -> int:
    """Prompt size for one request: fresh input + both cache tiers."""
    total = 0
    for key in (
        "input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            total += int(value)
    return total


def _scan_for_usage(text: str) -> tuple[int, str] | None:
    """Find the newest main-thread assistant message carrying usage.

    Returns (tokens, model) or None. Walks backwards so the first hit is the
    most recent one.
    """
    for raw in reversed(text.splitlines()):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or entry.get("isSidechain") is True:
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        tokens = _usage_tokens(usage)
        if tokens <= 0:
            continue
        model = message.get("model") or entry.get("model") or ""
        return tokens, str(model)
    return None


def read_context_usage(transcript_path: str) -> dict:
    """Current context fill from a transcript.

    Returns {"ok": bool, "tokens": int|None, "model": str, "reason": str|None}.
    Never raises: a hook that dies takes the turn's output with it.
    """
    if not transcript_path or not os.path.isfile(transcript_path):
        return {"ok": False, "tokens": None, "model": "", "reason": REASON_UNREADABLE}

    try:
        size = os.path.getsize(transcript_path)
    except OSError:
        return {"ok": False, "tokens": None, "model": "", "reason": REASON_UNREADABLE}

    for max_bytes in _TAIL_STEPS_BYTES:
        text = _read_tail(transcript_path, max_bytes)
        if not text:
            continue
        found = _scan_for_usage(text)
        if found is not None:
            tokens, model = found
            return {"ok": True, "tokens": tokens, "model": model, "reason": None}
        if max_bytes >= size:
            break  # whole file already scanned, larger steps read nothing new

    return {"ok": False, "tokens": None, "model": "", "reason": REASON_NO_USAGE}


def percent_full(
    tokens: int | None, window: int = DEFAULT_CONTEXT_WINDOW
) -> float | None:
    """Context fill as a percentage, rounded to one decimal. None if unknown."""
    if tokens is None or not isinstance(window, int) or window <= 0:
        return None
    return round(100.0 * tokens / window, 1)
