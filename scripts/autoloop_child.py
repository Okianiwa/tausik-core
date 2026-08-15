"""Everything about the child `claude -p` process.

Split out of autoloop_run.py when that module crossed the 400-line gate. The
seam is real, not cosmetic: this file knows how to spawn a Claude session and
read what came back, and knows nothing about queues, brakes or journals.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any
from pathlib import Path

import autoloop_git


PROMPT_TEMPLATE = Path(__file__).resolve().parent / "autoloop_prompt.md"


def build_prompt(
    task_slug: str,
    config: dict,
    template_path: Path = PROMPT_TEMPLATE,
    git_mode: str = autoloop_git.GIT_FULL,
    direction: str = "",
    handoff: str = "",
) -> str:
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError:
        template = (
            "Возьми задачу {task_slug}, доведи до закрытия через verify + "
            "task done --ac-verified, запиши итог через "
            '`python .claude/scripts/autoloop_handoff.py write "..."` и заверши '
            "турн. TAUSIK-сессию не открывай и не закрывай — она принадлежит "
            "человеку.\n{git_note}{handoff_note}"
        )
    prompt = template.format(
        task_slug=task_slug,
        soft_threshold=int(config.get("soft_threshold", 30)),
        git_note=autoloop_git.prompt_note(git_mode),
        handoff_note=handoff_section(handoff),
    )
    if direction:
        prompt += f"\n\n## Направление работы\n\n{direction}\n"
    return prompt


def handoff_section(handoff: str) -> str:
    """The previous iteration's note, pasted in rather than looked up.

    Handing it over through the prompt is what makes the run's own journal a
    working replacement for the session handoff: the next iteration gets the
    note whether or not it remembers to ask for one.
    """
    handoff = (handoff or "").strip()
    if not handoff:
        return ""
    return f"\n## Итог предыдущей итерации\n\n{handoff}\n"


def claude_command(
    prompt: str,
    project_dir: Path,
    model: str | None,
    git_mode: str = autoloop_git.GIT_FULL,
) -> list[str]:
    settings = Path(autoloop_git.build_profile(str(project_dir), git_mode))
    cmd = ["claude", "-p", prompt, "--output-format", "json"]
    if settings.exists():
        cmd += ["--settings", str(settings)]
    if model:
        cmd += ["--model", model]
    return cmd


def run_iteration(
    project_dir: Path,
    task_slug: str,
    config: dict,
    model: str | None,
    timeout: int,
    git_mode: str = autoloop_git.GIT_FULL,
    direction: str = "",
    handoff: str = "",
) -> dict:
    """One `claude -p` process. Returns a result dict; never raises."""
    prompt = build_prompt(
        task_slug, config, git_mode=git_mode, direction=direction, handoff=handoff
    )
    env = {
        **os.environ,
        "TAUSIK_AUTONOMY": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    env.pop("TAUSIK_SKIP_HOOKS", None)  # autonomy without gates is not a mode we offer

    try:
        completed = subprocess.run(
            claude_command(prompt, project_dir, model, git_mode),
            stdin=subprocess.DEVNULL,  # nothing to read; an inherited stdin can hang the run
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(project_dir),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "error": "timeout", "cost_usd": 0.0, "tokens": {}}
    except OSError as exc:
        return {
            "returncode": -1,
            "error": f"spawn_failed: {exc}",
            "cost_usd": 0.0,
            "tokens": {},
        }

    result: dict[str, Any] = {
        "returncode": completed.returncode,
        "error": None,
        "cost_usd": 0.0,
        "tokens": {},
    }
    payload = _parse_json_tail(completed.stdout)
    if payload:
        result["cost_usd"] = payload.get("total_cost_usd") or 0.0
        result["session_id"] = payload.get("session_id")
        result["num_turns"] = payload.get("num_turns")
        result["tokens"] = extract_tokens(payload)
        if payload.get("is_error"):
            result["error"] = payload.get("subtype") or "reported_error"
    elif completed.returncode != 0:
        result["error"] = (completed.stderr or "").strip()[:300] or "nonzero_exit"
    return result


def _parse_json_tail(stdout: str) -> dict | None:
    """`claude -p --output-format json` prints one JSON object; be forgiving anyway."""
    text = (stdout or "").strip()
    if not text:
        return None
    for candidate in (text, text.splitlines()[-1] if text.splitlines() else ""):
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


_TOKEN_FIELDS = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cache_read": "cache_read_input_tokens",
    "cache_write": "cache_creation_input_tokens",
}


def extract_tokens(payload: dict) -> dict:
    """Token counts from a `claude -p --output-format json` result.

    Reported rather than derived from cost: the price per token differs by model
    and cache tier, so working backwards from dollars would invent numbers.
    """
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {}
    tokens = {}
    for name, field in _TOKEN_FIELDS.items():
        value = usage.get(field)
        tokens[name] = int(value) if isinstance(value, (int, float)) else 0
    tokens["total"] = sum(tokens.values())
    return tokens


def _git(project_dir: Path, args: list[str]) -> str:
    """Read-only git query. Empty string when there is no repo — not every
    project the loop runs in is under version control."""
    try:
        result = subprocess.run(
            ["git", *args],
            stdin=subprocess.DEVNULL,  # nothing to read; an inherited stdin can hang the run
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            cwd=str(project_dir),
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def git_head(project_dir: Path) -> str:
    return _git(project_dir, ["rev-parse", "HEAD"])


def new_commits(project_dir: Path, head_before: str) -> list[str]:
    """Short SHAs created since `head_before`, newest last."""
    if not head_before:
        return []
    out = _git(project_dir, ["log", "--format=%h", f"{head_before}..HEAD"])
    return list(reversed(out.split())) if out else []
