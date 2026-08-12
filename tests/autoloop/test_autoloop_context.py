"""Context-fill measurement: the number the whole loop keys off."""

import json
import time

from autoloop.context import (
    REASON_NO_USAGE,
    REASON_UNREADABLE,
    percent_full,
    read_context_usage,
)
from conftest import assistant_entry


def test_counts_input_plus_both_cache_tiers(transcript):
    """Prompt size is input + cache_creation + cache_read, not input alone."""
    path = transcript(
        [assistant_entry(input_tokens=2, cache_creation=707, cache_read=104_609)]
    )

    usage = read_context_usage(path)

    assert usage["ok"] is True
    assert usage["tokens"] == 105_318
    assert usage["model"] == "claude-opus-5"


def test_uses_last_message_not_the_sum(transcript):
    """Cumulative spend is a different metric — the window holds only the last prompt."""
    path = transcript(
        [
            assistant_entry(cache_read=50_000),
            assistant_entry(cache_read=80_000),
            assistant_entry(cache_read=120_000),
        ]
    )

    assert read_context_usage(path)["tokens"] == 120_000


def test_skips_sidechain_entries(transcript):
    """Subagents have their own window; counting them reports a fill that never existed."""
    path = transcript(
        [
            assistant_entry(cache_read=30_000),
            assistant_entry(cache_read=900_000, isSidechain=True),
        ]
    )

    assert read_context_usage(path)["tokens"] == 30_000


def test_missing_file_is_not_an_error():
    usage = read_context_usage("no/such/transcript.jsonl")

    assert usage["ok"] is False
    assert usage["tokens"] is None
    assert usage["reason"] == REASON_UNREADABLE


def test_empty_path_is_not_an_error():
    assert read_context_usage("")["reason"] == REASON_UNREADABLE


def test_malformed_lines_are_skipped(tmp_path):
    """A half-written line at the tail must not hide the usable record above it."""
    path = tmp_path / "broken.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(assistant_entry(cache_read=12_345)) + "\n")
        f.write("{not json at all\n")
        f.write('{"type": "assistant", "message": {"usage": ')  # truncated mid-write

    usage = read_context_usage(str(path))

    assert usage["ok"] is True
    assert usage["tokens"] == 12_345


def test_transcript_without_usage_reports_no_usage(transcript):
    path = transcript([{"type": "user", "message": {"role": "user", "content": "hi"}}])

    usage = read_context_usage(path)

    assert usage["ok"] is False
    assert usage["tokens"] is None
    assert usage["reason"] == REASON_NO_USAGE


def test_zero_usage_entries_are_ignored(transcript):
    """A usage block of all zeros carries no information about window fill."""
    path = transcript(
        [
            assistant_entry(cache_read=7_000),
            assistant_entry(input_tokens=0, cache_read=0, cache_creation=0),
        ]
    )

    assert read_context_usage(path)["tokens"] == 7_000


def test_finds_usage_behind_a_large_tail_of_junk(tmp_path):
    """One tool result can exceed the first tail step; the reader must widen."""
    path = tmp_path / "padded.jsonl"
    filler = {"type": "user", "message": {"role": "user", "content": "x" * 20_000}}
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(assistant_entry(cache_read=55_555)) + "\n")
        for _ in range(20):  # ~400 KB, past the 64 KB first step
            f.write(json.dumps(filler) + "\n")

    assert read_context_usage(str(path))["tokens"] == 55_555


def test_large_transcript_stays_under_five_seconds(tmp_path):
    """AC: 50 MB transcript must not stall the turn — tail read, never full parse."""
    path = tmp_path / "huge.jsonl"
    filler = json.dumps(
        {"type": "user", "message": {"role": "user", "content": "y" * 50_000}}
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(assistant_entry(cache_read=1_000)) + "\n")
        for _ in range(1_000):  # ~50 MB
            f.write(filler + "\n")

    started = time.monotonic()
    usage = read_context_usage(str(path))
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, f"took {elapsed:.2f}s"
    # The one usable record is 50 MB up the file, past every tail step: the
    # reader must give up cleanly rather than degrade into a full-file parse.
    assert usage["reason"] == REASON_NO_USAGE


def test_percent_full_rounds_to_one_decimal():
    assert percent_full(105_318, 1_000_000) == 10.5
    assert percent_full(300_000, 1_000_000) == 30.0


def test_percent_full_handles_unknown_and_bad_window():
    assert percent_full(None, 1_000_000) is None
    assert percent_full(1_000, 0) is None
    assert percent_full(1_000, -5) is None
