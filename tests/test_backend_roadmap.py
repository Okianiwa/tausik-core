"""Roadmap visibility + live-children definition (scripts/backend_roadmap.py).

Regression suite for epic-done-irreversible-hides-tree. The original symptom:
`tausik roadmap` answered "No epics" against a 20-task DB, because an epic marked
done was dropped together with every live descendant. Silent — it did not fail,
it just returned a different answer, which is why the inconsistency survived
seven slices unnoticed.

Named to match the module so `verify --task` maps scoped pytest to it; see the
note in tests/test_service_hierarchy.py.

The guard that produces this state lives in service_hierarchy; the agreement
test at the bottom pins the two together.
"""

import pytest

from project_backend import SQLiteBackend
from project_service import ProjectService


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


# === Inconsistency is never hidden (the silent defect) ===


class TestRoadmapVisibility:
    def test_done_epic_with_live_children_still_visible(self, svc):
        """The original symptom: `roadmap` returned "No epics" with a full DB."""
        _epic_story_task(svc)
        svc.epic_done("ep", force=True)
        rm = svc.get_roadmap()
        assert rm, "done epic with live children must NOT vanish from the roadmap"
        assert rm[0]["inconsistent"] is True
        assert rm[0]["stories"][0]["tasks"][0]["slug"] == "t1", "live subtree must be reachable"

    def test_done_story_with_live_task_still_visible(self, svc):
        _epic_story_task(svc)
        svc.story_done("st", force=True)
        story = svc.get_roadmap()[0]["stories"][0]
        assert story["inconsistent"] is True
        assert story["tasks"][0]["slug"] == "t1"

    def test_consistent_done_epic_still_hidden(self, svc):
        """Guard-satisfying done epics keep the old behaviour — no noise."""
        _epic_story_task(svc)
        svc.task_done("t1", ac_verified=True, no_knowledge=True)
        svc.story_done("st")
        svc.epic_done("ep")
        assert svc.get_roadmap() == []
        assert len(svc.get_roadmap(include_done=True)) == 1

    def test_open_epic_not_flagged(self, svc):
        _epic_story_task(svc)
        assert svc.get_roadmap()[0]["inconsistent"] is False

    def test_reopen_clears_the_flag(self, svc):
        _epic_story_task(svc)
        svc.epic_done("ep", force=True)
        svc.epic_reopen("ep")
        assert svc.get_roadmap()[0]["inconsistent"] is False


# === Live-children lookups ===


class TestLiveChildren:
    def test_epic_live_children_names_stories_and_tasks(self, svc):
        _epic_story_task(svc)
        live = svc.be.epic_live_children("ep")
        assert [s["slug"] for s in live["stories"]] == ["st"]
        assert [t["slug"] for t in live["tasks"]] == ["t1"]

    def test_done_children_are_not_live(self, svc):
        _epic_story_task(svc)
        svc.task_done("t1", ac_verified=True, no_knowledge=True)
        svc.story_done("st")
        live = svc.be.epic_live_children("ep")
        assert live["stories"] == []
        assert live["tasks"] == []

    def test_story_live_children_scoped_to_its_own_tasks(self, svc):
        _epic_story_task(svc)
        svc.story_add("ep", "other", "Other story")
        svc.task_add("other", "t2", "Task 2")
        live = svc.be.story_live_children("st")
        assert [t["slug"] for t in live["tasks"]] == ["t1"], "must not leak sibling story's tasks"


# === One definition of "live" — guard and roadmap must never drift apart ===


def test_guard_and_roadmap_agree(svc):
    """If these two ever disagree, the bug returns in a new shape."""
    _epic_story_task(svc)
    live = svc.be.epic_live_children("ep")
    guard_sees_live = bool(live["stories"] or live["tasks"])

    svc.epic_done("ep", force=True)
    roadmap_flags = svc.get_roadmap()[0]["inconsistent"]

    assert guard_sees_live == roadmap_flags is True
