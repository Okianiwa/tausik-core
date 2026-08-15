"""What `verify` tells you to type next.

The handle is validated against a signed receipt. A project that never ran
`key init` has none, so `task done --verify-handle` refuses within the second —
and until this file existed, `verify` printed that exact command anyway. The
agent spent a closing attempt on an instruction the tool had just given it.
"""

import types

import pytest

import project_cli_verify as cli

REPORT = {"verify_handle": "42.deadbeef", "handle_expires_at": "2026-08-15T20:00:00Z"}


def out(capsys):
    return capsys.readouterr().out


# --- which command gets advised -------------------------------------------


def test_a_handle_backed_by_a_receipt_is_advised_as_a_command(capsys):
    """The working path must keep working: this is the whole point of handles."""
    cli._emit_handle(REPORT, "my-task", signed=True)
    printed = out(capsys)

    assert "--verify-handle 42.deadbeef" in printed
    assert "Present it" in printed


def test_a_handle_without_a_receipt_is_shown_but_not_advised(capsys):
    """AC negative, and the defect itself: the handle is real state and stays
    printed — hiding it would be hidden server state under another name. What
    is withheld is the command that cannot succeed."""
    cli._emit_handle(REPORT, "my-task", signed=False)
    printed = out(capsys)

    assert "42.deadbeef" in printed  # the handle exists and is still reported
    # Naming the flag while explaining the refusal is fine; handing it back as
    # an argument is the defect. So the assertion is on the command, not the word.
    assert "--verify-handle 42.deadbeef" not in printed
    assert "Present it" not in printed
    assert "task done my-task --ac-verified" in printed  # the command that works
    assert "key init" in printed  # and how to make handles usable here


def test_a_run_that_earned_no_handle_still_says_so(capsys):
    """Unchanged branch, guarded because it is one `if` away from the change."""
    cli._emit_handle({"verify_handle": None}, "my-task", signed=False)
    printed = out(capsys)

    assert "Verify handle: none" in printed
    assert "freshness" in printed


# --- where the receipt fact comes from ------------------------------------


def fake_service():
    return types.SimpleNamespace(be=types.SimpleNamespace(_conn=None, event_add=lambda *a: None))


@pytest.fixture
def receipt(monkeypatch):
    """Control what the stored receipt is, the way the validator sees it."""
    import verify_receipt_emit

    state = {"stored": None, "has_key": False}
    monkeypatch.setattr(verify_receipt_emit, "load_receipt", lambda conn, run_id: state["stored"])
    monkeypatch.setattr(cli, "_project_has_key", lambda svc: state["has_key"])
    return state


def test_a_signed_run_reports_true(receipt, capsys):
    receipt["stored"] = {"envelope": {"signature": {"key_fingerprint": "ab12"}}}

    assert cli._emit_receipt(fake_service(), 42) is True
    assert "signed" in out(capsys)


def test_a_keyless_run_reports_false(receipt, capsys):
    assert cli._emit_receipt(fake_service(), 42) is False
    assert "no project key" in out(capsys)


def test_a_configured_key_that_did_not_sign_also_reports_false(receipt, capsys):
    """AC negative: a key present but not signing is the silent-degradation
    case. Its handle is refused just the same, so the advice must not differ —
    while the warning about the degradation stays."""
    receipt["has_key"] = True

    assert cli._emit_receipt(fake_service(), 42) is False
    assert "WARNING" in out(capsys)


def test_an_unrecorded_run_reports_false(receipt, capsys):
    assert cli._emit_receipt(fake_service(), None) is False
    assert "not recorded" in out(capsys)
