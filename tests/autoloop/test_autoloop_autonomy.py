"""Autonomy profile: what turns it on, and what it refuses to relax."""

import json
import subprocess

import pytest

from autoloop import autonomy
from autoloop.autonomy import (
    REASON_DISABLED,
    REASON_HOOKS_DISABLED,
    REASON_INTERACTIVE,
    REASON_NO_MARKER,
    create_run_marker,
    is_enabled,
    issue_push_ticket,
    remove_run_marker,
    status,
    warn_if_misconfigured,
)
from conftest import AUTONOMY_PROFILE, LIB_HOOKS_DIR


@pytest.fixture
def armed(monkeypatch, project_dir):
    """A project with autonomy legitimately switched on."""
    monkeypatch.setenv("TAUSIK_AUTONOMY", "1")
    monkeypatch.delenv("TAUSIK_SKIP_HOOKS", raising=False)
    create_run_marker(str(project_dir), 4242)
    return project_dir


# --- what enables it ------------------------------------------------------


def test_flag_plus_marker_enables(armed):
    assert status(str(armed)) == (True, None)
    assert is_enabled(str(armed)) is True


def test_flag_without_marker_is_refused(monkeypatch, project_dir):
    """AC negative: a variable left over in someone's shell must not arm the loop."""
    monkeypatch.setenv("TAUSIK_AUTONOMY", "1")
    remove_run_marker(str(project_dir))

    enabled, reason = status(str(project_dir))

    assert enabled is False
    assert reason == REASON_NO_MARKER
    assert "метки прогона нет" in warn_if_misconfigured(str(project_dir))


def test_marker_without_flag_is_refused(monkeypatch, project_dir):
    monkeypatch.delenv("TAUSIK_AUTONOMY", raising=False)
    create_run_marker(str(project_dir), 1)

    assert status(str(project_dir)) == (False, REASON_DISABLED)
    assert warn_if_misconfigured(str(project_dir)) is None  # nothing to explain


def test_interactive_terminal_is_refused(monkeypatch, armed):
    """AC negative: a TTY means a human is watching — do not self-drive."""
    monkeypatch.setattr(autonomy, "_attached_to_terminal", lambda: True)

    enabled, reason = status(str(armed))

    assert enabled is False
    assert reason == REASON_INTERACTIVE
    assert "интерактивном терминале" in warn_if_misconfigured(str(armed))


def test_skip_hooks_disqualifies_autonomy(monkeypatch, armed):
    """An unattended agent with the gates switched off is not a mode we offer."""
    monkeypatch.setenv("TAUSIK_SKIP_HOOKS", "1")

    enabled, reason = status(str(armed))

    assert enabled is False
    assert reason == REASON_HOOKS_DISABLED
    assert "без гейтов" in warn_if_misconfigured(str(armed)).lower()


def test_marker_lifecycle(project_dir):
    assert create_run_marker(str(project_dir), 999) is True
    marker = project_dir / ".tausik" / ".autoloop.run"
    assert marker.read_text(encoding="utf-8") == "999"

    remove_run_marker(str(project_dir))
    assert not marker.exists()
    remove_run_marker(str(project_dir))  # idempotent: no raise on a second call


# --- push ticket ----------------------------------------------------------


def test_push_ticket_requires_autonomy(monkeypatch, project_dir):
    """Without autonomy the ticket is never minted — the human still confirms."""
    monkeypatch.delenv("TAUSIK_AUTONOMY", raising=False)
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a))

    assert issue_push_ticket(str(project_dir)) is False
    assert called == []


def test_push_ticket_invokes_the_library_cli(monkeypatch, armed):
    """The sanctioned path: `tausik push-ok`, not a patched library gate."""
    cli = armed / ".tausik" / ("tausik.cmd" if autonomy.sys.platform == "win32" else "tausik")
    cli.write_text("", encoding="utf-8")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert issue_push_ticket(str(armed), ttl=30) is True
    assert seen["cmd"][1:] == ["push-ok", "--ttl", "30"]
    assert seen["cwd"] == str(armed)


def test_push_ticket_reports_cli_failure(monkeypatch, armed):
    cli = armed / ".tausik" / ("tausik.cmd" if autonomy.sys.platform == "win32" else "tausik")
    cli.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, "", "boom"),
    )

    assert issue_push_ticket(str(armed)) is False


def test_push_ticket_survives_a_hanging_cli(monkeypatch, armed):
    cli = armed / ".tausik" / ("tausik.cmd" if autonomy.sys.platform == "win32" else "tausik")
    cli.write_text("", encoding="utf-8")

    def hang(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 15)

    monkeypatch.setattr(subprocess, "run", hang)

    assert issue_push_ticket(str(armed)) is False


def test_push_ticket_without_a_cli_present(armed):
    assert issue_push_ticket(str(armed)) is False


# --- what autonomy must NOT touch ----------------------------------------


def test_library_hooks_are_not_patched():
    """AC: the fork checkout stays pristine — edits there die on the next /fab update."""
    from pathlib import Path

    lib_hooks = LIB_HOOKS_DIR
    for name in (
        "git_push_gate.py",
        "bash_firewall.py",
        "task_gate.py",
        "secret_scan.py",
    ):
        source = (lib_hooks / name).read_text(encoding="utf-8", errors="replace")
        assert "TAUSIK_AUTONOMY" not in source, f"{name} was patched for autonomy"


def test_dangerous_git_still_blocked_under_autonomy(armed):
    """AC negative: force-push stays blocked — autonomy removes confirmation, not guards."""
    import importlib.util
    from pathlib import Path

    firewall_path = LIB_HOOKS_DIR / "bash_firewall.py"
    spec = importlib.util.spec_from_file_location("bash_firewall_under_test", firewall_path)
    firewall = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(firewall)

    for command in ("git push --force origin main", "git reset --hard HEAD~3"):
        assert any(regex.search(command) for regex, _ in firewall.WARN_PATTERNS_RE), (
            f"{command!r} slipped past the firewall"
        )


def test_autonomy_settings_profile_is_shaped_correctly():
    """The headless permission profile must not hand out a blanket allow."""
    profile = json.loads(AUTONOMY_PROFILE.read_text(encoding="utf-8"))
    allow = profile["permissions"]["allow"]
    deny = profile["permissions"]["deny"]

    assert "Bash(git commit:*)" in allow
    assert "Bash(git push:*)" in allow
    assert any("--force" in rule for rule in deny)
    assert not any(rule.strip() in ("Bash", "Bash(*)", "*") for rule in allow)
