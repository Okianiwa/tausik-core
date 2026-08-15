"""Git modes and the guards around starting a run."""

import argparse
import json
import subprocess
from pathlib import Path

import pytest

import autoloop_run as autoloop
from autoloop import autonomy
from autoloop_child import build_prompt, claude_command
from autoloop_git import GIT_COMMIT, GIT_FULL, GIT_OFF, build_profile, prompt_note
from .conftest import SKILLS_DIR

BASE_PROFILE = {
    "permissions": {
        "allow": [
            "Bash(pytest:*)",
            "Bash(git status:*)",
            "Bash(git add:*)",
            "Bash(git commit:*)",
            "Bash(git push:*)",
            "Bash(git branch:*)",
            "Bash(git checkout -b:*)",
            "Write",
        ],
        "deny": ["Bash(git push --force:*)"],
    }
}


@pytest.fixture
def project_with_profile(project_dir):
    claude_dir = project_dir / ".claude"
    claude_dir.mkdir(exist_ok=True)
    (claude_dir / "settings.autonomy.json").write_text(json.dumps(BASE_PROFILE), encoding="utf-8")
    return project_dir


def allow_list(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))["permissions"]["allow"]


def deny_list(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))["permissions"]["deny"]


# --- git modes ------------------------------------------------------------


def test_full_mode_keeps_every_git_command(project_with_profile):
    """Full mode is generated like the others now — it has to carry the session
    guard — but it must still take nothing away from git."""
    path = build_profile(str(project_with_profile), GIT_FULL)

    assert path.endswith("settings.git-full.json")
    assert "Bash(git push:*)" in allow_list(path)
    assert "Bash(git commit:*)" in allow_list(path)


def test_commit_mode_drops_push_only(project_with_profile):
    path = build_profile(str(project_with_profile), GIT_COMMIT)
    allow = allow_list(path)

    assert "Bash(git commit:*)" in allow
    assert "Bash(git push:*)" not in allow
    assert "Bash(git push:*)" in deny_list(path)


def test_off_mode_removes_every_writing_git_command(project_with_profile):
    """AC negative: with git off the commands are absent from the allow-list,
    so the ban holds even if the agent ignores the prompt."""
    path = build_profile(str(project_with_profile), GIT_OFF)
    allow = allow_list(path)

    for rule in ("Bash(git add:*)", "Bash(git commit:*)", "Bash(git push:*)"):
        assert rule not in allow, rule
        assert rule in deny_list(path), rule
    assert "Bash(git status:*)" in allow  # reading the repo stays fine
    assert "Bash(pytest:*)" in allow


def test_generated_profile_never_overwrites_the_base(project_with_profile):
    build_profile(str(project_with_profile), GIT_OFF)

    base = json.loads(
        (project_with_profile / ".claude" / "settings.autonomy.json").read_text(encoding="utf-8")
    )
    assert "Bash(git push:*)" in base["permissions"]["allow"]


def test_generated_profiles_live_apart_from_the_session_readings(
    project_with_profile,
):
    """AC: the readings directory holds readings; profiles go one level down."""
    from autoloop.state import state_dir

    readings = Path(state_dir(str(project_with_profile)))
    for mode in (GIT_COMMIT, GIT_OFF):
        path = Path(build_profile(str(project_with_profile), mode))

        assert path.parent == readings / "profiles", mode
        assert path.name == f"settings.git-{mode}.json"
    assert list(readings.glob("*.json")) == []


def test_unreadable_base_profile_falls_back(project_dir):
    """No profile to derive from — hand back the base path rather than crash."""
    path = build_profile(str(project_dir), GIT_OFF)

    assert path.endswith("settings.autonomy.json")


def test_command_points_at_the_mode_profile(project_with_profile):
    cmd = claude_command("p", Path(project_with_profile), None, GIT_OFF)

    settings = cmd[cmd.index("--settings") + 1]
    assert settings.endswith("settings.git-off.json")


@pytest.mark.parametrize("mode", [GIT_FULL, GIT_COMMIT, GIT_OFF])
def test_prompt_states_the_git_rule(mode, project_with_profile):
    prompt = build_prompt("task-a", {"soft_threshold": 30}, git_mode=mode)

    assert prompt_note(mode) in prompt


def test_off_mode_prompt_says_so_plainly():
    assert "не трогай" in prompt_note(GIT_OFF).lower()
    assert "пушить" in prompt_note(GIT_COMMIT).lower()


def test_direction_reaches_the_prompt():
    prompt = build_prompt(
        "task-a", {"soft_threshold": 30}, direction="сначала фронтенд, потом тесты"
    )

    assert "сначала фронтенд, потом тесты" in prompt
    assert "Направление работы" in prompt


def test_prompt_without_direction_has_no_empty_section():
    assert "Направление работы" not in build_prompt("task-a", {"soft_threshold": 30})


# --- guards ---------------------------------------------------------------


@pytest.fixture
def loop_env(monkeypatch, project_dir):
    monkeypatch.setattr(autoloop, "PROJECT_DIR", Path(project_dir))
    monkeypatch.setattr(autoloop, "tausik_cli", lambda _dir: "fake-tausik")
    calls = {"claude": []}

    def fake_cli(project, args, timeout=30):
        if args[:2] == ["task", "list"]:
            status = args[args.index("--status") + 1]
            if status == "planning":
                return "slug title status\n----\ntask-a Title planning\n"
        return "slug title status\n----\n"

    def fake_run(cmd, **kwargs):
        if cmd and cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 1, "", "")
        calls["claude"].append(cmd)
        return subprocess.CompletedProcess(cmd, 0, '{"is_error": false}', "")

    monkeypatch.setattr(autoloop, "_run_cli", fake_cli)
    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def run_loop(**overrides):
    args = argparse.Namespace(
        command="run",
        max_iterations=1,
        max_idle=None,
        max_crashes=None,
        model=None,
        timeout=60,
        dry_run=False,
        git_mode=GIT_FULL,
        direction="",
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return autoloop.loop(args)


def test_second_run_is_refused_while_one_is_live(loop_env, project_dir, capsys):
    """AC negative: two supervisors on one project would fight over the queue."""
    autonomy.create_run_marker(str(project_dir), 999)

    code = run_loop()
    out = capsys.readouterr().out

    assert code == autoloop.EXIT_STOPPED
    assert loop_env["claude"] == []
    assert "прогон уже идёт" in out
    assert "autoloop.stop" in out  # tells the human how to stop the live one


def test_refusal_leaves_the_existing_marker_intact(loop_env, project_dir):
    """The refusing run must not clean up after the run it refused to join."""
    autonomy.create_run_marker(str(project_dir), 999)

    run_loop()

    marker = Path(autonomy.run_marker_path(str(project_dir)))
    assert marker.exists()
    assert marker.read_text(encoding="utf-8") == "999"


def test_run_starts_when_no_marker_is_present(loop_env, project_dir):
    assert run_loop() == autoloop.EXIT_STOPPED  # stopped by max_iterations=1
    assert len(loop_env["claude"]) == 1


def test_git_mode_flag_is_parsed():
    args = autoloop.build_parser().parse_args(["run", "--git-mode", "off"])

    assert args.git_mode == GIT_OFF


def test_git_mode_defaults_to_full():
    assert autoloop.build_parser().parse_args(["run"]).git_mode == GIT_FULL


def test_unknown_git_mode_is_rejected():
    with pytest.raises(SystemExit):
        autoloop.build_parser().parse_args(["run", "--git-mode", "yolo"])


def test_direction_flag_is_parsed():
    args = autoloop.build_parser().parse_args(["run", "--direction", "фронтенд"])

    assert args.direction == "фронтенд"


@pytest.mark.parametrize("command", ["run", "report", "watch"])
def test_all_subcommands_exist(command):
    assert autoloop.build_parser().parse_args([command]).command == command


# --- skill definition -----------------------------------------------------


def test_skill_file_documents_every_verb():
    """The skill is the user-facing contract; each verb must be written down."""
    skill = (SKILLS_DIR / "auto" / "SKILL.md").read_text(encoding="utf-8")

    for verb in ("стоп", "stop", "отчёт", "report", "статус", "watch"):
        assert verb in skill, verb
    for flag in ("--git-mode", "--direction", "--max-iterations"):
        assert flag in skill, flag
    assert "autoloop_run.py" in skill


def test_the_branch_is_chosen_by_the_argument_and_by_nothing_else():
    """Measured failure, not a hypothetical: the skill used to state the
    criterion twice. The table picks the branch by a word in the argument; a
    paragraph three lines above picked it by the human's circumstances — "the
    path for when the human leaves". The prose is read first, so a request to
    work in the chat that mentioned «меня не будет» started headless agents,
    closed the human's session and let an agent decide alone.

    A second criterion anywhere in this file is the defect, regardless of how
    it is worded — hence the phrase list rather than one exact string.
    """
    skill = (SKILLS_DIR / "auto" / "SKILL.md").read_text(encoding="utf-8")

    for phrase in ("когда человек уходит", "человек уходит", "не хочет занимать"):
        assert phrase not in skill, phrase


def test_absence_of_the_human_routes_to_the_chat_branch():
    """The phrases that misfired must be answered in the file, not left to be
    inferred: naming them costs one table row and removes the inference."""
    skill = (SKILLS_DIR / "auto" / "SKILL.md").read_text(encoding="utf-8")

    rows = [line for line in skill.splitlines() if line.startswith("|") and "меня не будет" in line]
    assert rows, "фразы отсутствия человека не названы в таблице разбора"
    assert "в этом чате" in rows[0].lower(), rows[0]
