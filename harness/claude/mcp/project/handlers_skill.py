"""TAUSIK MCP handlers — skill lifecycle, maintenance, helpers.

Split from handlers.py to stay under 400 lines per file.
"""

from __future__ import annotations

import os
from typing import Any

from tausik_utils import tausik_config_path


def _project_dir() -> str:
    """Get project root directory."""
    return os.getcwd()


def _get_ide_skills_dir(project_dir: str) -> str:
    """Resolve IDE skills directory using ide_utils or fallback."""
    try:
        from ide_utils import detect_ide, get_skills_dir

        ide = detect_ide(project_dir)
        return get_skills_dir(project_dir, ide)
    except ImportError:
        return os.path.join(project_dir, ".claude", "skills")


def _get_agents_skills_dir(project_dir: str) -> str:
    """Resolve source agents skills directory using ide_utils or fallback."""
    try:
        from ide_utils import detect_ide, get_agents_skills_dir

        ide = detect_ide(project_dir)
        return get_agents_skills_dir(project_dir, ide)
    except ImportError:
        return os.path.join(project_dir, "harness", "claude", "skills")


# --- Skill activate / deactivate / list ---


def _skill_paths() -> dict[str, str]:
    """Build common skill-related paths. Reduces boilerplate."""
    project_dir = _project_dir()
    return {
        "project_dir": project_dir,
        "vendor_dir": os.path.join(project_dir, ".tausik", "vendor"),
        "tausik_dir": os.path.join(project_dir, ".tausik"),
        "config_path": tausik_config_path(project_dir),
        "skills_dst": _get_ide_skills_dir(project_dir),
        "lib_skills": _get_agents_skills_dir(project_dir),
    }


def handle_skill_list() -> str:
    p = _skill_paths()
    try:
        from project_service import ProjectService

        data = ProjectService.skill_list(p["vendor_dir"], p["skills_dst"])
    except Exception as e:
        return f"Error: {e}"
    lines = []
    active_names: set[str] = set()
    for s in sorted(data.get("active", []), key=lambda x: x["name"]):
        lines.append(f"[ACTIVE  ] {s['name']}")
        active_names.add(s["name"])
    vendored_names: set[str] = set()
    for s in sorted(data.get("vendored", []), key=lambda x: x["name"]):
        lines.append(f"[VENDORED] {s['name']}")
        vendored_names.add(s["name"])
    # Show available skills from repos (not yet installed)
    try:
        from skill_repos import repo_list_all_skills

        for s in repo_list_all_skills(p["vendor_dir"]):
            if s["name"] not in active_names and s["name"] not in vendored_names:
                desc = f" — {s['description']}" if s.get("description") else ""
                lines.append(f"[AVAILABLE] {s['name']} ({s['repo']}){desc}")
    except Exception:
        pass  # Non-fatal — repo listing is optional
    return "\n".join(lines) if lines else "(none)"


def handle_skill_catalog(repo_name: str | None = None, as_json: bool = False) -> str:
    """Discovery view: skills offered by configured/cloned skill repos."""
    import json as _json

    p = _skill_paths()
    try:
        from project_service import ProjectService

        rows = ProjectService.skill_catalog(
            p["vendor_dir"], repo_name=repo_name, config_path=p["config_path"]
        )
    except Exception as e:
        return f"Error: {e}"
    if as_json:
        return _json.dumps(rows, ensure_ascii=False, indent=2)
    if not rows:
        return (
            f"(no skills found in repo '{repo_name}')"
            if repo_name
            else "(no skill repos cloned — run `tausik_skill_repo_add`)"
        )
    lines = []
    for r in rows:
        cat = f" [{r['category']}]" if r.get("category") else ""
        desc = (r.get("description") or "").splitlines()[0][:80]
        lines.append(f"{r['name']}{cat} ({r['repo']}) — {desc}")
    return "\n".join(lines)


def handle_skill_activate(svc: Any, name: str) -> str:
    p = _skill_paths()
    try:
        from project_service import ProjectService

        return ProjectService.skill_activate(
            name, p["vendor_dir"], p["skills_dst"], p["lib_skills"], p["config_path"]
        )
    except Exception as e:
        return f"Error: {e}"


def handle_skill_deactivate(svc: Any, name: str) -> str:
    p = _skill_paths()
    try:
        from project_service import ProjectService

        return ProjectService.skill_deactivate(
            name, p["skills_dst"], p["lib_skills"], p["config_path"]
        )
    except Exception as e:
        return f"Error: {e}"


# --- Skill install / uninstall ---


def handle_skill_install(name: str) -> str:
    p = _skill_paths()
    try:
        from project_service import ProjectService

        return ProjectService.skill_install(
            name, p["vendor_dir"], p["skills_dst"], p["config_path"], p["tausik_dir"]
        )
    except Exception as e:
        return f"Error: {e}"


def handle_skill_uninstall(name: str) -> str:
    p = _skill_paths()
    try:
        from project_service import ProjectService

        return ProjectService.skill_uninstall(name, p["skills_dst"], p["config_path"])
    except Exception as e:
        return f"Error: {e}"


# --- Skill repo management ---


def handle_skill_repo_add(url: str, force: bool = False) -> str:
    p = _skill_paths()
    try:
        from skill_repos import repo_add

        return repo_add(url, p["vendor_dir"], p["config_path"], force=force)
    except Exception as e:
        return f"Error: {e}"


def handle_skill_repo_remove(name: str) -> str:
    p = _skill_paths()
    try:
        from skill_repos import repo_remove

        return repo_remove(name, p["vendor_dir"], p["config_path"])
    except Exception as e:
        return f"Error: {e}"


def handle_skill_repo_list() -> str:
    p = _skill_paths()
    try:
        from skill_repos import repo_list

        repos = repo_list(p["vendor_dir"], p["config_path"])
        if not repos:
            return "No skill repos configured. Add one: tausik_skill_repo_add"
        lines = []
        for r in repos:
            status = "cloned" if r["cloned"] else "not cloned"
            default = " (default)" if r.get("default") else ""
            lines.append(f"{r['name']}{default} [{status}] — {r['url']}")
            if r["skills"]:
                lines.append(f"  Skills: {', '.join(r['skills'])}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


# --- Maintenance ---


def handle_update_claudemd(svc) -> str:
    # Content assembly is shared with the CLI path (service_claudemd) — an MCP-only
    # copy once silently erased the memory tail (mcp-update-claudemd-drops-memory-tail).
    from service_claudemd import (
        build_dynamic_content,
        replace_dynamic_section,
        resolve_branch,
        resolve_version,
    )

    project_dir = _project_dir()

    tail_warnings: list[str] = []
    dynamic_content = build_dynamic_content(
        svc, resolve_branch(project_dir), resolve_version(), warnings=tail_warnings
    )

    # Find and update CLAUDE.md
    claudemd = None
    try:
        from ide_utils import detect_ide, get_ide_dir

        _ide = detect_ide(project_dir)
        _ide_dir = get_ide_dir(project_dir, _ide)
        _ide_candidates = [
            os.path.join(project_dir, "CLAUDE.md"),
            os.path.join(_ide_dir, "CLAUDE.md"),
        ]
    except ImportError:
        _ide_candidates = [
            os.path.join(project_dir, "CLAUDE.md"),
            os.path.join(project_dir, ".claude", "CLAUDE.md"),
        ]
    for candidate in _ide_candidates:
        if os.path.exists(candidate):
            claudemd = candidate
            break
    if not claudemd:
        return "Warning: CLAUDE.md not found."

    with open(claudemd, encoding="utf-8") as f:
        content = f.read()

    new_content = replace_dynamic_section(content, dynamic_content)
    if new_content is None:
        return "Warning: <!-- DYNAMIC:START --> marker not found in CLAUDE.md"

    with open(claudemd, "w", encoding="utf-8") as f:
        f.write(new_content)
    result = f"CLAUDE.md updated ({claudemd})."
    for w in tail_warnings:
        result += f"\nWarning: {w}"
    return result


def handle_list(items: list, fmt, empty_msg: str = "None.") -> str:
    """Format a list of items with a formatter function."""
    if not items:
        return empty_msg
    return "\n".join(fmt(item) for item in items)
