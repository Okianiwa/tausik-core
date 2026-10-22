"""Tests for scripts/mcp_reaper.py — sibling-MCP reporting (decision #189).

The framework NEVER kills a process: the fresh forensic evidence (2026-07-18,
Win11 26200) showed every "sibling" MCP server is a LIVE server.py under a LIVE
claude.exe in the same Code.exe window — indistinguishable from a stale one by
the process tree, so any auto-killer risks tearing down a live session. These
helpers therefore only (a) turn a sibling count into a hard warning above a
threshold, and (b) memoize the expensive enumeration behind a TTL so it does
not re-spawn a PowerShell probe on every self_check call.
"""

from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from mcp_reaper import (  # noqa: E402
    SIBLING_WARN_THRESHOLD,
    cached_enumerate,
    refresh_in_background,
    sibling_warning,
)


class TestSiblingWarning:
    """AC2 — report a threshold-crossing accumulation; never claim a kill."""

    def test_over_threshold_warns(self):
        msg = sibling_warning(SIBLING_WARN_THRESHOLD + 1)
        assert msg  # non-empty
        assert str(SIBLING_WARN_THRESHOLD + 1) in msg
        # Report-only contract (decision #189): the message states the framework
        # will NOT kill anything — it must not promise or imply an automatic reap.
        assert "will not kill" in msg.lower()
        assert "reap" not in msg.lower()

    def test_at_or_below_threshold_is_silent(self):
        # NEGATIVE: a normal number of siblings must not raise a warning.
        assert sibling_warning(SIBLING_WARN_THRESHOLD) == ""
        assert sibling_warning(0) == ""
        assert sibling_warning(1) == ""

    def test_introspection_failure_is_silent(self):
        # count == -1 means "unknown"; the caller renders that state itself.
        assert sibling_warning(-1) == ""

    def test_non_int_is_silent(self):
        assert sibling_warning(None) == ""  # type: ignore[arg-type]
        assert sibling_warning("5") == ""  # type: ignore[arg-type]

    def test_custom_threshold(self):
        assert sibling_warning(2, threshold=5) == ""
        assert sibling_warning(6, threshold=5) != ""


class TestCachedEnumerate:
    """AC3 — the enumeration is not re-run on every call within the TTL window."""

    def test_first_call_runs_and_caches(self):
        calls = {"n": 0}

        def enum():
            calls["n"] += 1
            return {"count": 2}

        cache: dict = {}
        got = cached_enumerate("proj", enum, ttl=30.0, now=100.0, cache=cache)
        assert got == {"count": 2}
        assert calls["n"] == 1

    def test_within_ttl_reuses_cache(self):
        calls = {"n": 0}

        def enum():
            calls["n"] += 1
            return {"count": calls["n"]}

        cache: dict = {}
        first = cached_enumerate("proj", enum, ttl=30.0, now=100.0, cache=cache)
        # 5s later — inside the 30s window: NO fresh enumeration.
        again = cached_enumerate("proj", enum, ttl=30.0, now=105.0, cache=cache)
        assert first == again
        assert calls["n"] == 1  # enum ran exactly once

    def test_after_ttl_re_enumerates(self):
        calls = {"n": 0}

        def enum():
            calls["n"] += 1
            return {"count": calls["n"]}

        cache: dict = {}
        cached_enumerate("proj", enum, ttl=30.0, now=100.0, cache=cache)
        # 31s later — window expired: enumerate again.
        cached_enumerate("proj", enum, ttl=30.0, now=131.0, cache=cache)
        assert calls["n"] == 2

    def test_distinct_keys_isolated(self):
        calls = {"n": 0}

        def enum():
            calls["n"] += 1
            return calls["n"]

        cache: dict = {}
        a = cached_enumerate("projA", enum, ttl=30.0, now=100.0, cache=cache)
        b = cached_enumerate("projB", enum, ttl=30.0, now=100.0, cache=cache)
        assert a != b  # different keys → separate enumerations
        assert calls["n"] == 2

    @staticmethod
    def _unknown(value: dict) -> bool:
        return value["count"] == -1

    def test_sticky_value_outlives_ttl(self):
        # A refused probe (WMI denied: ~5.7s) is not re-paid when the window expires.
        calls = {"n": 0}

        def enum():
            calls["n"] += 1
            return {"count": -1}

        cache: dict = {}
        cached_enumerate("proj", enum, ttl=30.0, now=100.0, cache=cache, sticky=self._unknown)
        cached_enumerate("proj", enum, ttl=30.0, now=1000.0, cache=cache, sticky=self._unknown)
        assert calls["n"] == 1

    def test_non_sticky_value_still_expires(self):
        calls = {"n": 0}

        def enum():
            calls["n"] += 1
            return {"count": 0}

        cache: dict = {}
        cached_enumerate("proj", enum, ttl=30.0, now=100.0, cache=cache, sticky=self._unknown)
        cached_enumerate("proj", enum, ttl=30.0, now=131.0, cache=cache, sticky=self._unknown)
        assert calls["n"] == 2


class TestRefreshInBackground:
    """A slow probe runs once, off the caller's thread, however many callers wait on it."""

    def test_callers_during_a_run_share_one_thread(self):
        release = threading.Event()
        calls = {"n": 0}

        def probe():
            calls["n"] += 1
            release.wait(5)

        inflight: dict = {}
        try:
            first = refresh_in_background("proj", probe, inflight=inflight)
            second = refresh_in_background("proj", probe, inflight=inflight)
            assert first is second
        finally:
            release.set()
        first.join(5)
        assert not first.is_alive()
        assert calls["n"] == 1

    def test_finished_run_is_replaced_by_a_new_one(self):
        calls = {"n": 0}

        def probe():
            calls["n"] += 1

        inflight: dict = {}
        first = refresh_in_background("proj", probe, inflight=inflight)
        first.join(5)
        second = refresh_in_background("proj", probe, inflight=inflight)
        second.join(5)
        assert first is not second
        assert calls["n"] == 2
