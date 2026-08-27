"""Declared harness bookkeeping is exempt from Rule 1 — and nothing else is.

The defect this closes: an agent harness writes its own state inside the tree
it works on (a checkpoint pointer the compaction guard reads back, a compaction
log). Both write gates classify a target only as «inside the tree / outside»,
so such a file is judged as source and refused once the last task is closed.
The refusal is not the damage — the damage is that the bookkeeping stops while
every step of it still reports success.

The exemption is DECLARED, never guessed: the list ships empty, and the tests
below spend as much effort on what must stay blocked as on what is let through,
because an exclusion wider than it reads is how Rule 1 would quietly end.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

import pytest
from conftest import canonical_ddl

_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
_HOOKS = os.path.join(_SCRIPTS, "hooks")
if _HOOKS not in sys.path:
    sys.path.insert(0, _HOOKS)

from _common import gate_exclude_globs, path_is_excluded  # noqa: E402

TASK_GATE = os.path.join(_HOOKS, "task_gate.py")
BASH_GATE = os.path.join(_HOOKS, "bash_write_gate.py")

POINTER = ".claude/.checkpoint-host-556a4872"


@pytest.fixture(autouse=True)
def _без_хостовой_переменной(monkeypatch):
    """Настройка машины не должна попадать в замер.

    `TAUSIK_GATE_EXCLUDE_GLOBS` — переменная окружения, и на машине, где
    оболочка ею пользуется, она стоит в профиле сессии. Внутрипроцессные
    проверки её подхватывали, и «без конфига исключений нет» мерило профиль
    хоста, а не код. Поймано этим же тестом на первом прогоне после того, как
    переменную прописали в `~/.claude/settings.json`.
    """
    monkeypatch.delenv("TAUSIK_GATE_EXCLUDE_GLOBS", raising=False)


def _project(tmp_path, *, exclude=None, active=False):
    tausik = tmp_path / ".tausik"
    tausik.mkdir(exist_ok=True)
    if exclude is not None:
        (tausik / "config.json").write_text(
            json.dumps({"gates": {"exclude_globs": exclude}}), encoding="utf-8"
        )
    conn = sqlite3.connect(str(tausik / "tausik.db"))
    conn.execute(canonical_ddl("tasks"))
    conn.execute(
        "INSERT INTO tasks (slug, title, status, scope_paths, created_at, updated_at) "
        "VALUES ('t', 't', ?, NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        ("active" if active else "done",),
    )
    conn.commit()
    conn.close()
    return tmp_path


def _run(hook, project_dir, payload, env_extra=None):
    env = os.environ.copy()
    for k in ("TAUSIK_SKIP_HOOKS", "TAUSIK_HOOK_FAIL_OPEN", "TAUSIK_HOOK_FAIL_SECURE",
              "TAUSIK_GATE_EXCLUDE_GLOBS"):
        env.pop(k, None)
    env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, hook], input=json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=10,
    )


def _write(path):
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": "x"}}


def _bash(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}}


# ── the list itself ─────────────────────────────────────────────────────────

def test_no_config_no_exclusions(tmp_path):
    assert gate_exclude_globs(str(tmp_path)) == []


def test_config_and_env_are_additive(tmp_path, monkeypatch):
    _project(tmp_path, exclude=[".claude/.checkpoint-*"])
    monkeypatch.setenv("TAUSIK_GATE_EXCLUDE_GLOBS", f".claude/compact-summaries/**{os.pathsep}")
    assert gate_exclude_globs(str(tmp_path)) == [
        ".claude/.checkpoint-*",
        ".claude/compact-summaries/**",
    ]


def test_catch_all_patterns_are_dropped(tmp_path):
    # A rule switched off by one line of config is not a rule: `**` here would
    # be the cheapest way out of any block, so it is refused rather than obeyed.
    _project(tmp_path, exclude=["**", "*", "**/*", "*/*", "/", ".claude/.checkpoint-*"])
    assert gate_exclude_globs(str(tmp_path)) == [".claude/.checkpoint-*"]


def test_malformed_config_yields_no_exclusions(tmp_path):
    _project(tmp_path)
    (tmp_path / ".tausik" / "config.json").write_text("{not json", encoding="utf-8")
    assert gate_exclude_globs(str(tmp_path)) == []


def test_non_string_entries_ignored(tmp_path):
    _project(tmp_path, exclude=[".claude/.checkpoint-*", 42, None, {"a": 1}])
    assert gate_exclude_globs(str(tmp_path)) == [".claude/.checkpoint-*"]


# ── what a pattern actually covers ──────────────────────────────────────────

def test_star_does_not_cross_a_slash(tmp_path):
    # The one that matters: `.claude/*` written to exempt one state file must
    # not also exempt `.claude/hooks/task_gate.py` — the gate's own code.
    globs = [".claude/*"]
    root = str(tmp_path)
    assert path_is_excluded(".claude/.checkpoint-x", root, globs)
    assert not path_is_excluded(".claude/hooks/task_gate.py", root, globs)


def test_double_star_spans_directories(tmp_path):
    globs = [".claude/compact-summaries/**"]
    root = str(tmp_path)
    assert path_is_excluded(".claude/compact-summaries/2026.md", root, globs)
    assert path_is_excluded(".claude/compact-summaries/a/b/c.md", root, globs)
    assert not path_is_excluded(".claude/settings.json", root, globs)


def test_absolute_target_is_matched(tmp_path):
    assert path_is_excluded(str(tmp_path / POINTER), str(tmp_path), [".claude/.checkpoint-*"])


def test_path_outside_the_project_is_not_excluded(tmp_path):
    # Jurisdiction over out-of-tree paths is a separate question each gate
    # answers on its own; answering it here too would make one loosening
    # depend on the other.
    outside = str(tmp_path.parent / "elsewhere" / ".claude" / ".checkpoint-x")
    assert not path_is_excluded(outside, str(tmp_path), [".claude/.checkpoint-*"])


# ── task_gate: Write/Edit ───────────────────────────────────────────────────

def test_pointer_blocked_without_the_exclusion(tmp_path):
    proj = _project(tmp_path)
    res = _run(TASK_GATE, proj, _write(POINTER))
    assert res.returncode == 2, res.stderr


def test_pointer_allowed_with_the_exclusion(tmp_path):
    proj = _project(tmp_path, exclude=[".claude/.checkpoint-*"])
    res = _run(TASK_GATE, proj, _write(POINTER))
    assert res.returncode == 0, res.stderr


def test_exclusion_via_env_alone(tmp_path):
    proj = _project(tmp_path)
    res = _run(TASK_GATE, proj, _write(POINTER),
               {"TAUSIK_GATE_EXCLUDE_GLOBS": ".claude/.checkpoint-*"})
    assert res.returncode == 0, res.stderr


def test_project_code_stays_blocked(tmp_path):
    # The whole point: the exclusion buys the pointer, not the source tree.
    proj = _project(tmp_path, exclude=[".claude/.checkpoint-*"])
    res = _run(TASK_GATE, proj, _write("app/Foo.php"))
    assert res.returncode == 2
    assert "No active task" in res.stderr


def test_one_unexcluded_target_keeps_the_gate_on(tmp_path):
    # FileSystem move names two paths. Fail-closed: all of them or none.
    proj = _project(tmp_path, exclude=[".claude/.checkpoint-*"])
    payload = {"tool_name": "FileSystem",
               "tool_input": {"file_path": POINTER, "destination": "app/Foo.php"}}
    assert _run(TASK_GATE, proj, payload).returncode == 2


def test_exclusion_is_not_needed_when_a_task_is_active(tmp_path):
    proj = _project(tmp_path, active=True)
    assert _run(TASK_GATE, proj, _write(POINTER)).returncode == 0


# ── bash_write_gate: the same verdict through the shell ─────────────────────

def test_bash_pointer_blocked_without_the_exclusion(tmp_path):
    proj = _project(tmp_path)
    res = _run(BASH_GATE, proj, _bash(f'echo "{{}}" > {POINTER}'))
    assert res.returncode == 2, res.stderr


def test_bash_pointer_allowed_with_the_exclusion(tmp_path):
    proj = _project(tmp_path, exclude=[".claude/.checkpoint-*"])
    res = _run(BASH_GATE, proj, _bash(f'echo "{{}}" > {POINTER}'))
    assert res.returncode == 0, res.stderr


def test_bash_project_code_stays_blocked(tmp_path):
    proj = _project(tmp_path, exclude=[".claude/.checkpoint-*"])
    res = _run(BASH_GATE, proj, _bash("echo x > app/Foo.php"))
    assert res.returncode == 2


def test_bash_mixed_targets_block_on_the_unexcluded_one(tmp_path):
    proj = _project(tmp_path, exclude=[".claude/.checkpoint-*"])
    res = _run(BASH_GATE, proj, _bash(f"echo x > {POINTER} && echo y > app/Foo.php"))
    assert res.returncode == 2
    # QG-0 lists targets with the platform's separator (the Rule-2 branch
    # normalises, this one does not) — the assertion is about WHICH file is
    # named, not about how the host spells a path.
    assert res.stderr.replace("\\", "/").count("app/Foo.php") == 1
    # The excluded path must not be listed as an offender: a block that names
    # a file it did not object to sends the reader after the wrong one.
    assert ".checkpoint-" not in res.stderr
