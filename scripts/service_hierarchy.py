"""TAUSIK HierarchyMixin -- epic/story CRUD with validation.

Extracted from project_service.py for filesize compliance
(epic-done-irreversible-hides-tree). Mixed into ProjectService; relies on the
composed class for `be`, `_require_epic` and `_require_story`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tausik_utils import ServiceError, validate_length, validate_slug

if TYPE_CHECKING:
    from project_backend import SQLiteBackend


_CHILD_LABELS = (("stories", "story"), ("tasks", "task"))


def _format_live_children(children: dict[str, list[dict[str, Any]]]) -> str:
    """Name the culprits, one per line -- an unnamed refusal is unactionable."""
    lines: list[str] = []
    for kind, label in _CHILD_LABELS:
        for row in children.get(kind, []):
            lines.append(f"  - {label} '{row['slug']}' [{row['status']}]: {row['title']}")
    return "\n".join(lines)


class HierarchyMixin:
    """Epic/story CRUD with validation."""

    be: SQLiteBackend

    def epic_add(self, slug: str, title: str, description: str | None = None) -> str:
        from tausik_utils import safe_single_line

        validate_slug(slug)
        validate_length("title", title)
        title = safe_single_line(title) or title
        self.be.epic_add(slug, title, safe_single_line(description))
        return f"Epic '{slug}' created."

    def epic_list(self) -> list[dict[str, Any]]:
        return self.be.epic_list()

    def epic_done(self, slug: str, force: bool = False) -> str:
        self._require_epic(slug)
        if not force:
            live = self.be.epic_live_children(slug)
            if live["stories"] or live["tasks"]:
                raise ServiceError(
                    f"Epic '{slug}' has live children:\n"
                    f"{_format_live_children(live)}\n"
                    "Close them first, or pass --force to mark the epic done anyway "
                    "(the roadmap will flag the inconsistency)."
                )
        self.be.epic_update(slug, status="done")
        return f"Epic '{slug}' marked done."

    def epic_reopen(self, slug: str) -> str:
        epic = self._require_epic(slug)
        if epic["status"] == "active":
            return f"Epic '{slug}' is already open."
        self.be.epic_update(slug, status="active")
        return f"Epic '{slug}' reopened."

    def epic_delete(self, slug: str) -> str:
        self._require_epic(slug)
        self.be.epic_delete(slug)
        return f"Epic '{slug}' deleted."

    def story_add(
        self, epic_slug: str, slug: str, title: str, description: str | None = None
    ) -> str:
        from tausik_utils import safe_single_line

        self._require_epic(epic_slug)
        validate_slug(slug)
        validate_length("title", title)
        title = safe_single_line(title) or title
        self.be.story_add(epic_slug, slug, title, safe_single_line(description))
        return f"Story '{slug}' created in epic '{epic_slug}'."

    def story_list(self, epic_slug: str | None = None) -> list[dict[str, Any]]:
        return self.be.story_list(epic_slug)

    def story_done(self, slug: str, force: bool = False) -> str:
        self._require_story(slug)
        if not force:
            live = self.be.story_live_children(slug)
            if live["tasks"]:
                raise ServiceError(
                    f"Story '{slug}' has live children:\n"
                    f"{_format_live_children(live)}\n"
                    "Close them first, or pass --force to mark the story done anyway "
                    "(the roadmap will flag the inconsistency)."
                )
        self.be.story_update(slug, status="done")
        return f"Story '{slug}' marked done."

    def story_reopen(self, slug: str) -> str:
        story = self._require_story(slug)
        if story["status"] != "done":
            return f"Story '{slug}' is already open."
        self.be.story_update(slug, status="active")
        return f"Story '{slug}' reopened."

    def story_delete(self, slug: str) -> str:
        self._require_story(slug)
        self.be.story_delete(slug)
        return f"Story '{slug}' deleted."
