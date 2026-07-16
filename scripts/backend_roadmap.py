"""TAUSIK backend -- roadmap tree and hierarchy consistency.

Extracted from backend_queries.py for filesize compliance
(epic-done-irreversible-hides-tree). Mixed into SQLiteBackend via
BackendQueriesMixin.

SINGLE SOURCE OF TRUTH for "live children". The epic_done/story_done guard and
the roadmap renderer must agree on what "live" means. If the two definitions
drift, an epic can be refused as non-empty by the guard yet hidden as done by
the roadmap -- which is the exact bug this module was split out to fix. Change
the definition here and both move together.

"Live" = not done. Archiving needs no clause of its own: `hygiene archive` only
ever stamps archived_at on rows already `status='done'` (project_cli_hygiene
`_archive_apply`), and task_update's whitelist refuses the column outright — so
archived implies done, and `status!='done'` already excludes every archived row.
An extra `archived_at IS NULL` here would be unreachable by construction.
"""

from __future__ import annotations

from typing import Any

_LIVE_STORIES_SQL = (
    "SELECT s.slug, s.title, s.status FROM stories s "
    "JOIN epics e ON s.epic_id=e.id "
    "WHERE e.slug=? AND s.status!='done' ORDER BY s.created_at"
)

_LIVE_TASKS_OF_EPIC_SQL = (
    "SELECT t.slug, t.title, t.status FROM tasks t "
    "JOIN stories s ON t.story_id=s.id "
    "JOIN epics e ON s.epic_id=e.id "
    "WHERE e.slug=? AND t.status!='done' ORDER BY t.created_at"
)

_LIVE_TASKS_OF_STORY_SQL = (
    "SELECT t.slug, t.title, t.status FROM tasks t "
    "JOIN stories s ON t.story_id=s.id "
    "WHERE s.slug=? AND t.status!='done' ORDER BY t.created_at"
)


class BackendRoadmapMixin:
    """Roadmap tree + live-children lookups.

    Mixed into SQLiteBackend through BackendQueriesMixin; relies on the base
    class for the `_q` query helper.
    """

    def epic_live_children(self, slug: str) -> dict[str, list[dict[str, Any]]]:
        return {
            "stories": self._q(_LIVE_STORIES_SQL, (slug,)),
            "tasks": self._q(_LIVE_TASKS_OF_EPIC_SQL, (slug,)),
        }

    def story_live_children(self, slug: str) -> dict[str, list[dict[str, Any]]]:
        return {"stories": [], "tasks": self._q(_LIVE_TASKS_OF_STORY_SQL, (slug,))}

    def get_roadmap_data(self, include_done: bool = False) -> list[dict[str, Any]]:
        task_filter = "" if include_done else "WHERE t.status != 'done'"
        all_tasks = self._q(
            "SELECT t.*, s.slug AS story_slug, s.title AS story_title, "
            "s.status AS story_status, e.slug AS epic_slug, e.title AS epic_title, "
            "e.status AS epic_status "
            "FROM tasks t "
            "LEFT JOIN stories s ON t.story_id=s.id "
            "LEFT JOIN epics e ON s.epic_id=e.id "
            f"{task_filter} "
            "ORDER BY e.created_at, s.created_at, t.created_at"
        )
        epics = {e["slug"]: e for e in self.epic_list()}
        stories_by_epic: dict[str, list[dict[str, Any]]] = {}
        for s in self._q(
            "SELECT s.*, e.slug AS epic_slug FROM stories s "
            "JOIN epics e ON s.epic_id=e.id ORDER BY s.created_at"
        ):
            stories_by_epic.setdefault(s["epic_slug"], []).append(s)

        tree: dict[str, dict[str, Any]] = {}
        task_map: dict[str, list[dict[str, Any]]] = {}

        for t in all_tasks:
            ss = t.get("story_slug")
            if ss:
                task_map.setdefault(ss, []).append(t)

        for epic_slug, epic in epics.items():
            # Queried rather than derived from task_map: task_map's contents
            # depend on include_done, so deriving would make "live" mean
            # different things per call -- the drift this module exists to stop.
            inconsistent = epic["status"] == "done" and self._has_live(
                self.epic_live_children(epic_slug)
            )
            if not include_done and epic["status"] == "done" and not inconsistent:
                continue
            epic_data: dict[str, Any] = {**epic, "stories": [], "inconsistent": inconsistent}
            for story in stories_by_epic.get(epic_slug, []):
                s_inconsistent = story["status"] == "done" and self._has_live(
                    self.story_live_children(story["slug"])
                )
                if not include_done and story["status"] == "done" and not s_inconsistent:
                    continue
                tasks = task_map.get(story["slug"], [])
                if not include_done:
                    tasks = [t for t in tasks if t["status"] != "done"]
                epic_data["stories"].append(
                    {**story, "tasks": tasks, "inconsistent": s_inconsistent}
                )
            tree[epic_slug] = epic_data

        return list(tree.values())

    @staticmethod
    def _has_live(children: dict[str, list[dict[str, Any]]]) -> bool:
        return bool(children["stories"] or children["tasks"])
