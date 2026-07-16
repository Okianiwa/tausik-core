"""epic/story done guard + reopen (scripts/service_hierarchy.py).

Regression suite for epic-done-irreversible-hides-tree — `epic done` used to be
irreversible and accepted live children silently.

Named to match the module so `verify --task` / `task done` map scoped pytest to
it via the basename heuristic (gate_test_resolver). A test file the resolver
cannot reach turns a future edit of this module into a silent [SKIP] — the exact
class of failure this task exists to remove.

The negative tests (guard refuses) are the load-bearing ones per convention #22:
a guard nobody proved refuses is a guard that might be a no-op. Both directions
were checked by deliberate mutation — disabling the guard fails these tests.

Roadmap visibility lives in tests/test_backend_roadmap.py, next to the module
that owns the "live children" definition.
"""

import pytest

from project_backend import SQLiteBackend
from project_service import ProjectService, ServiceError


@pytest.fixture
def svc(tmp_path):
    be = SQLiteBackend(str(tmp_path / "test.db"))
    s = ProjectService(be)
    yield s
    be.close()


def _epic_story_task(svc):
    svc.epic_add("ep", "Epic")
    svc.story_add("ep", "st", "Story")
    svc.task_add("st", "t1", "Task 1")


# === Guard: refuses on live children (convention #22 — the negative case) ===


class TestDoneGuard:
    def test_epic_done_refused_with_live_story(self, svc):
        svc.epic_add("ep", "Epic")
        svc.story_add("ep", "st", "Story")
        with pytest.raises(ServiceError, match="live children"):
            svc.epic_done("ep")
        assert svc.epic_list()[0]["status"] == "active", "epic must stay open after refusal"

    def test_epic_done_refused_with_live_task(self, svc):
        _epic_story_task(svc)
        with pytest.raises(ServiceError) as exc:
            svc.epic_done("ep")
        # Culprits named, not just counted — an unnamed refusal is unactionable.
        assert "story 'st'" in str(exc.value)
        assert "task 't1'" in str(exc.value)

    def test_story_done_refused_with_live_task(self, svc):
        _epic_story_task(svc)
        with pytest.raises(ServiceError) as exc:
            svc.story_done("st")
        assert "task 't1'" in str(exc.value)
        assert svc.story_list()[0]["status"] != "done"

    def test_epic_done_allowed_when_children_done(self, svc):
        _epic_story_task(svc)
        svc.task_done("t1", ac_verified=True, no_knowledge=True)
        svc.story_done("st")
        assert "done" in svc.epic_done("ep")
        assert svc.epic_list()[0]["status"] == "done"

    def test_epic_done_allowed_when_childless(self, svc):
        svc.epic_add("ep", "Epic")
        assert "done" in svc.epic_done("ep")

    def test_force_overrides_guard(self, svc):
        _epic_story_task(svc)
        assert "done" in svc.epic_done("ep", force=True)
        assert svc.epic_list()[0]["status"] == "done"


# === Reopen: no one-way doors ===


class TestReopen:
    def test_epic_reopen_after_force(self, svc):
        _epic_story_task(svc)
        svc.epic_done("ep", force=True)
        assert "reopened" in svc.epic_reopen("ep")
        assert svc.epic_list()[0]["status"] == "active"

    def test_story_reopen(self, svc):
        svc.epic_add("ep", "Epic")
        svc.story_add("ep", "st", "Story")
        svc.story_done("st")
        assert "reopened" in svc.story_reopen("st")
        assert svc.story_list()[0]["status"] == "active"

    def test_reopen_is_idempotent(self, svc):
        svc.epic_add("ep", "Epic")
        assert "already open" in svc.epic_reopen("ep")

    def test_reopen_unknown_raises(self, svc):
        with pytest.raises(ServiceError, match="not found"):
            svc.epic_reopen("nope")
        with pytest.raises(ServiceError, match="not found"):
            svc.story_reopen("nope")
