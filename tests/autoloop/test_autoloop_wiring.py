"""How the mechanism reaches a project — and how it stays off until asked.

The library ships these hooks to every project it touches. That is the whole
point of the move (a project-local copy dies on the next redeploy), and also
its one real hazard: a chat that starts wiping its own conversation because an
update arrived is not a feature anyone agreed to. So registration is universal
and activation is not.
"""

import json
import os
import sys

import chat_watch as hook
from conftest import AUTONOMY_PROFILE

sys.path.insert(0, str(AUTONOMY_PROFILE.parents[2] / "bootstrap"))

from bootstrap_copy import copy_autonomy_profile  # noqa: E402
from bootstrap_hooks import build_hooks_dict  # noqa: E402


def commands(hooks, event):
    return [entry["command"] for group in hooks.get(event, []) for entry in group.get("hooks", [])]


# --- registration ---------------------------------------------------------


def test_the_watcher_is_registered_on_session_start():
    hooks = build_hooks_dict(lambda script, suffix="": f"python /abs/{script}{suffix}")

    assert any("chat_watch.py" in cmd for cmd in commands(hooks, "SessionStart"))


def test_every_stop_hook_of_the_mechanism_is_registered():
    """The measurement, the autonomous exit, and the readiness flag all hang off
    Stop. A settings.json regenerated without one of them looks fine and quietly
    stops working."""
    hooks = build_hooks_dict(lambda script, suffix="": f"python /abs/{script}{suffix}")
    stop = commands(hooks, "Stop")

    for script in ("sensor.py", "exit_guard.py", "chat_ready.py"):
        assert any(script in cmd for cmd in stop), f"{script} missing from Stop"


def test_the_autoloop_package_is_addressed_separately_from_hooks():
    """sensor.py and exit_guard.py live beside hooks/, not inside it."""
    hooks = build_hooks_dict(
        lambda script, suffix="": f"python /hooks/{script}{suffix}",
        lambda script: f"python /scripts/autoloop/{script}",
    )
    stop = commands(hooks, "Stop")

    assert "python /scripts/autoloop/sensor.py" in stop
    assert any(cmd == "python /hooks/chat_ready.py" for cmd in stop)


# --- the autonomy profile -------------------------------------------------


def test_the_autonomy_profile_is_installed(tmp_path):
    lib = tmp_path / "lib"
    (lib / "harness" / "claude").mkdir(parents=True)
    (lib / "harness" / "claude" / "settings.autonomy.json").write_text(
        '{"permissions": {"allow": [], "deny": []}}', encoding="utf-8"
    )
    target = tmp_path / ".claude"
    target.mkdir()

    assert copy_autonomy_profile(str(lib), str(target), "claude") == 1
    assert (target / "settings.autonomy.json").exists()


def test_an_edited_profile_is_never_overwritten(tmp_path):
    """AC negative: the file is a human-readable allow-list. A redeploy that
    silently replaced it would widen what an unattended run may do."""
    lib = tmp_path / "lib"
    (lib / "harness" / "claude").mkdir(parents=True)
    (lib / "harness" / "claude" / "settings.autonomy.json").write_text(
        '{"permissions": {"allow": ["everything"]}}', encoding="utf-8"
    )
    target = tmp_path / ".claude"
    target.mkdir()
    mine = target / "settings.autonomy.json"
    mine.write_text('{"permissions": {"allow": []}}', encoding="utf-8")

    assert copy_autonomy_profile(str(lib), str(target), "claude") == 0
    assert "everything" not in mine.read_text(encoding="utf-8")


# --- activation -----------------------------------------------------------


def write_config(project_dir, config):
    path = os.path.join(str(project_dir), ".tausik", "config.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f)


def test_a_project_that_asked_for_it_is_watched(project_dir):
    write_config(project_dir, {"autoloop": {"watch": True}})

    assert hook.watch_enabled(str(project_dir)) is True


def test_a_project_that_did_not_ask_is_left_alone(project_dir):
    """AC negative: the hook arrives everywhere; the cleaning does not. A chat
    wiping itself because a library update landed is a lost conversation."""
    write_config(project_dir, {"autoloop": {"soft_threshold": 30}})

    assert hook.watch_enabled(str(project_dir)) is False


def test_no_config_at_all_means_no_watching(project_dir):
    assert hook.watch_enabled(str(project_dir)) is False


def test_a_broken_config_means_no_watching(project_dir):
    """A typo in a settings file must not be read as consent."""
    path = os.path.join(str(project_dir), ".tausik", "config.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ not json")

    assert hook.watch_enabled(str(project_dir)) is False


def test_a_truthy_value_is_not_a_yes(project_dir):
    """`"watch": "later"` is a note to self, not an opt-in."""
    write_config(project_dir, {"autoloop": {"watch": "later"}})

    assert hook.watch_enabled(str(project_dir)) is False


def test_the_hook_does_not_spawn_a_watcher_when_switched_off(project_dir, monkeypatch):
    write_config(project_dir, {"autoloop": {"watch": False}})
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.setattr(hook, "payload", lambda: {"transcript_path": "x.jsonl"})
    spawned = []
    monkeypatch.setattr(hook, "owning_chat", lambda *_a, **_k: spawned.append(1))

    assert hook.main() == 0
    assert spawned == []
    assert not (project_dir / ".tausik" / ".chat.started").exists()
