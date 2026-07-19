"""session-end-call-budget: capacity-гейт разделяет budget<=cap (session end помогает) от
budget>cap (session end бесполезен — свежая сессия даёт те же cap)."""

from __future__ import annotations

import os
import sys

import pytest

_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from service_recording import check_session_capacity  # noqa: E402
from tausik_utils import ServiceError  # noqa: E402


@pytest.fixture(autouse=True)
def _fixed_cap(monkeypatch):
    """cap=200 фиксирован — тест не зависит от .tausik/config.json."""
    import project_config

    monkeypatch.setattr(project_config, "load_config", lambda: {"session_capacity_calls": 200})


class _FakeBackend:
    def __init__(self, remaining, session=1):
        self._remaining = remaining
        self._session = session

    def session_capacity_summary(self, capacity):
        return {
            "session": self._session,
            "capacity": capacity,
            "used": 0,
            "planned_active": 0,
            "remaining": self._remaining,
        }


def test_budget_over_cap_omits_session_end_advice():
    """AC#3 негатив: budget > cap (200) → НЕ советует session end; называет вместимость + split."""
    be = _FakeBackend(remaining=200)  # cap=200; budget 400 > cap
    with pytest.raises(ServiceError) as ei:
        check_session_capacity(be, "huge", {"call_budget": 400})
    msg = str(ei.value).lower()
    assert "session end" not in msg, "budget>cap: session end бесполезен — не советуем (тупик)"
    assert "capacity" in msg and "split" in msg


def test_budget_under_cap_exhausted_keeps_session_end_advice():
    """AC#4 позитив: budget <= cap, но remaining<budget (сессия израсходована) → совет session end."""
    be = _FakeBackend(remaining=40)  # cap=200; budget 100 <= cap, > remaining=40
    with pytest.raises(ServiceError) as ei:
        check_session_capacity(be, "t", {"call_budget": 100})
    assert "session end" in str(ei.value).lower()


def test_budget_fits_no_error():
    """budget <= remaining → гейт не срабатывает."""
    be = _FakeBackend(remaining=200)
    check_session_capacity(be, "t", {"call_budget": 50})  # не должно бросить


def test_no_budget_no_error():
    """Без call_budget гейт неприменим."""
    be = _FakeBackend(remaining=0)
    check_session_capacity(be, "t", {})
