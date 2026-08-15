"""Test model routing suggestion — phase x complexity matrix (v15mr-phase-matrix)."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from model_routing import VALID_PHASES, format_suggestion, suggest_model


# --- Back-compat: single-arg call defaults to phase=implement -----------------


def test_default_phase_is_implement():
    # No phase arg must equal an explicit implement phase (AC2).
    assert suggest_model("medium") == suggest_model("medium", "implement")


def test_implement_simple_maps_to_sonnet():
    # Deliberate v1.5 change (Decision #112): implement floor is Sonnet, not Haiku.
    r = suggest_model("simple")
    assert "sonnet" in r["model"].lower()
    assert "Sonnet" in r["display"]


@pytest.mark.parametrize(
    "complexity,expected_model",
    [
        pytest.param("simple", "sonnet", id="implement_simple_sonnet"),
        pytest.param("medium", "sonnet", id="implement_medium_sonnet"),
        pytest.param("complex", "opus", id="implement_complex_opus"),
        pytest.param("SIMPLE", "sonnet", id="case_insensitive"),
        pytest.param("  medium  ", "sonnet", id="whitespace_tolerated"),
    ],
)
def test_implement_mapping(complexity, expected_model):
    r = suggest_model(complexity)
    assert expected_model in r["model"].lower()


# --- AC1: matrix cells match the ТЗ ------------------------------------------


@pytest.mark.parametrize(
    "phase,complexity,expected_model",
    [
        # planning: complexity-independent -> fable
        pytest.param("planning", "simple", "fable", id="planning_simple_fable"),
        pytest.param("planning", "medium", "fable", id="planning_medium_fable"),
        pytest.param("planning", "complex", "fable", id="planning_complex_fable"),
        # implement
        pytest.param("implement", "simple", "sonnet", id="implement_simple"),
        pytest.param("implement", "complex", "opus", id="implement_complex"),
        # research: simple=haiku (its new home), deep=sonnet
        pytest.param("research", "simple", "haiku", id="research_simple_haiku"),
        pytest.param("research", "medium", "sonnet", id="research_medium_sonnet"),
        pytest.param("research", "complex", "sonnet", id="research_complex_sonnet"),
    ],
)
def test_matrix_cells(phase, complexity, expected_model):
    r = suggest_model(complexity, phase)
    assert expected_model in r["model"].lower()


def test_phase_is_case_insensitive():
    assert suggest_model("simple", "RESEARCH") == suggest_model("simple", "research")


# --- AC3: negative — unknown phase raises with the valid list -----------------


def test_unknown_phase_raises_valueerror():
    with pytest.raises(ValueError) as exc:
        suggest_model("medium", "deployment")
    msg = str(exc.value)
    for ph in VALID_PHASES:
        assert ph in msg


# --- Complexity fallbacks (preserved behaviour) ------------------------------


def test_none_defaults_to_medium_with_hint():
    r = suggest_model(None)  # implement/medium -> sonnet
    assert "sonnet" in r["model"].lower()
    assert (
        "not specified" in r["rationale"].lower()
        or "not set" in r["rationale"].lower()
        or "defaulting" in r["rationale"].lower()
    )


def test_unknown_complexity_falls_back_with_warning():
    r = suggest_model("gigantic")  # implement/medium -> sonnet
    assert "sonnet" in r["model"].lower()
    assert "unknown" in r["rationale"].lower()


def test_none_complexity_in_research_phase():
    r = suggest_model(None, "research")  # research/medium -> sonnet
    assert "sonnet" in r["model"].lower()


# --- AC4: config override ----------------------------------------------------


def test_config_override_per_tier():
    cfg = {"model_routing": {"implement": {"complex": "claude-fable-5"}}}
    r = suggest_model("complex", "implement", config=cfg)
    assert r["model"] == "claude-fable-5"
    assert "Fable" in r["display"]
    assert "override" in r["rationale"].lower()


def test_config_override_whole_phase_string():
    cfg = {"model_routing": {"planning": "claude-opus-4-8"}}
    r = suggest_model("simple", "planning", config=cfg)
    assert r["model"] == "claude-opus-4-8"
    assert "Opus" in r["display"]


def test_config_override_unknown_model_id_used_as_display():
    cfg = {"model_routing": {"implement": {"medium": "gpt-some-overlay"}}}
    r = suggest_model("medium", "implement", config=cfg)
    assert r["model"] == "gpt-some-overlay"
    assert r["display"] == "gpt-some-overlay"  # unknown family -> id verbatim


def test_malformed_override_ignored_base_matrix_wins():
    for bad in ({"model_routing": "nope"}, {"model_routing": {"implement": 123}}, {}):
        r = suggest_model("complex", "implement", config=bad)
        assert "opus" in r["model"].lower()  # base matrix cell, no raise


def test_no_config_means_no_override():
    # config=None must NOT auto-load / apply any override (pure call).
    r = suggest_model("complex", "implement")
    assert "opus" in r["model"].lower()


# --- l26-config-not-repo-state-audit: override provenance in the rationale ----
#
# The rationale must name the tier that ACTUALLY holds the override, not always
# ".tausik/config.json" — a value from the user/managed tier lives in a
# different file, and sending the user to edit the repo file is a dead end.

import json as _json  # noqa: E402

_OVR = {"model_routing": {"implement": {"complex": "claude-fable-5"}}}


def _cfg_file(tmp_path, name, cfg):
    p = tmp_path / name
    p.write_text(_json.dumps(cfg), encoding="utf-8")
    return str(p)


def test_override_provenance_names_managed_tier(tmp_path, monkeypatch):
    monkeypatch.setenv("TAUSIK_MANAGED_CONFIG", _cfg_file(tmp_path, "managed.json", _OVR))
    monkeypatch.setenv("TAUSIK_USER_CONFIG", str(tmp_path / "absent-user.json"))
    r = suggest_model("complex", "implement", config=_OVR)
    assert r["model"] == "claude-fable-5"
    assert "managed tier" in r["rationale"]
    assert "$TAUSIK_MANAGED_CONFIG" in r["rationale"]


def test_override_provenance_names_user_tier(tmp_path, monkeypatch):
    # managed is checked first; with it absent, the user tier must be named.
    monkeypatch.setenv("TAUSIK_MANAGED_CONFIG", str(tmp_path / "absent-managed.json"))
    monkeypatch.setenv("TAUSIK_USER_CONFIG", _cfg_file(tmp_path, "user.json", _OVR))
    r = suggest_model("complex", "implement", config=_OVR)
    assert "user tier" in r["rationale"]
    assert "~/.tausik/config.json" in r["rationale"]


def test_override_provenance_names_repo_file_for_project_tier(tmp_path, monkeypatch):
    """By ELIMINATION (s128 review HIGH-2): an applied override absent from both
    per-machine trusted tiers came from the project's own .tausik/config.json —
    determined WITHOUT reading the project config from the ambient cwd (#265)."""
    monkeypatch.setenv("TAUSIK_MANAGED_CONFIG", str(tmp_path / "absent-managed.json"))
    monkeypatch.setenv("TAUSIK_USER_CONFIG", str(tmp_path / "absent-user.json"))
    r = suggest_model("complex", "implement", config=_OVR)
    assert ".tausik/config.json" in r["rationale"]
    assert "managed tier" not in r["rationale"] and "user tier" not in r["rationale"]


def test_override_provenance_does_not_read_project_config_from_cwd(monkeypatch):
    """Regression for the #265 violation the review caught: provenance must NOT
    resolve the project tier from the ambient cwd. If it ever calls
    load_project_config again, this blows up loudly instead of silently
    misattributing across projects."""
    import project_config

    def _boom(*_a, **_k):
        raise AssertionError("provenance read project config from cwd (#265 regression)")

    monkeypatch.setattr(project_config, "load_project_config", _boom)
    monkeypatch.delenv("TAUSIK_MANAGED_CONFIG", raising=False)
    monkeypatch.delenv("TAUSIK_USER_CONFIG", raising=False)
    r = suggest_model("complex", "implement", config=_OVR)
    assert "override" in r["rationale"].lower()  # no crash, override still applied


def test_override_provenance_neutral_when_tiers_unreadable(monkeypatch):
    """If the trusted tiers cannot be inspected at all, name NO specific file —
    a false path is worse than a neutral phrase."""
    import config_trust

    def _raise():
        raise OSError("trusted tiers unreadable")

    monkeypatch.setattr(config_trust, "raw_layers", _raise)
    r = suggest_model("complex", "implement", config=_OVR)
    assert "a config tier (project/user/managed)" in r["rationale"]
    assert ".tausik/config.json" not in r["rationale"]


# --- format_suggestion -------------------------------------------------------


def test_format_suggestion_is_one_line():
    # config={} keeps the test hermetic — no read of the real .tausik/config.json (H1).
    s = format_suggestion("simple", config={})
    assert "\n" not in s
    assert "Sonnet" in s


def test_format_suggestion_honours_phase_and_config():
    s = format_suggestion("simple", "research", config={"model_routing": {}})
    assert "Haiku" in s


def test_return_dict_has_stable_keys():
    r = suggest_model("simple")
    assert set(r.keys()) == {"model", "display", "rationale"}


# --- Review-fix guards (v15mr-review-fixes, Decision #112 follow-up) ----------


def test_multi_token_model_id_is_ambiguous_none():
    from model_routing import _model_family

    # >1 family token -> ambiguous -> None (M1), never a silent first-match guess.
    assert _model_family("claude-sonnet-opus-x") is None
    assert _model_family("claude-opus-4-8") == "opus"  # single token still resolves


def test_override_future_pointrelease_keeps_honest_display():
    # H2: a same-family but unregistered version must show its own id, not lie.
    cfg = {"model_routing": {"implement": {"complex": "claude-opus-4-9"}}}
    r = suggest_model("complex", "implement", config=cfg)
    assert r["model"] == "claude-opus-4-9"
    assert r["display"] == "claude-opus-4-9"
    assert r["display"] != "Opus 4.8"


def test_valid_phases_derived_from_matrix():
    # M2: VALID_PHASES is the matrix's own keys (single source of truth).
    assert set(VALID_PHASES) == {"planning", "implement", "research"}


# --- Family-agnostic routing: GLM/z.ai (Decision #119, axis-2) ----------------


def test_family_none_defaults_to_claude():
    # Back-compat: no family arg → canonical Claude ids (matches all other tests).
    assert suggest_model("complex", "implement")["model"] == "claude-opus-4-8"
    assert suggest_model("complex", "implement", family=None)["model"] == "claude-opus-4-8"
    assert suggest_model("complex", "implement", family="claude")["model"] == "claude-opus-4-8"


def test_glm_family_resolves_glm_ids():
    # complex implement → flagship rank → GLM's flagship model, not a Claude id.
    r = suggest_model("complex", "implement", family="glm")
    assert r["model"] == "glm-4.6"
    assert "claude" not in r["model"]
    # simple implement → sonnet rank → GLM's sonnet-rank model.
    assert suggest_model("simple", "implement", family="glm")["model"] == "glm-4.6"


def test_glm_family_via_config_override_models():
    cfg = {"model_profiles": {"families": {"glm": {"opus": {"model": "glm-5.2"}}}}}
    assert suggest_model("complex", "implement", config=cfg, family="glm")["model"] == "glm-5.2"


def test_nonexistent_family_falls_back_to_claude():
    # NEGATIVE: unknown family must not raise — falls back to the claude spec.
    assert suggest_model("complex", "implement", family="nonexistent")["model"] == "claude-opus-4-8"


def test_model_tier_resolves_glm_via_profiles():
    from model_profiles import load_families
    from model_routing_matrix import _model_tier

    fams = load_families(None)
    assert _model_tier("glm-4.6", fams) == 3  # fable rank (highest it fills)
    assert _model_tier("glm-4.6") is None  # without profiles → unknown (back-compat)
    assert _model_tier("claude-opus-4-8") == 2  # claude token path unchanged


# --- finding the transcript at all ------------------------------------------
#
# Everything above hands the banner an `active_model`. In a real session nobody
# does: it has to FIND the transcript, and for a long while it could not — the
# only finder lived behind `hooks.session_metrics`, which cannot resolve its own
# sibling `token_rows` when imported as a package. The ImportError was swallowed,
# every task_start printed "active model unknown", and the mismatch warning could
# not fire at all. A banner that never warns looks exactly like a banner with
# nothing to warn about.


class TestTranscriptDiscovery:
    @staticmethod
    def _deployed_session(tmp_path, monkeypatch, model="claude-opus-5"):
        """A project, its transcript folder, and a session speaking into it."""
        import json

        import autoloop_presence as presence

        project = tmp_path / "proj"
        (project / ".tausik").mkdir(parents=True)
        folder = tmp_path / ".claude" / "projects" / "D--proj"
        folder.mkdir(parents=True)
        transcript = folder / "live.jsonl"
        transcript.write_text(
            json.dumps({"message": {"model": model, "usage": {"input_tokens": 10}}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            presence.os.path, "expanduser", lambda p: p.replace("~", str(tmp_path), 1)
        )
        monkeypatch.setattr(presence, "project_slug", lambda _d: "D--proj")
        monkeypatch.chdir(project)
        return transcript

    def test_the_running_session_is_found(self, tmp_path, monkeypatch):
        from model_routing import _auto_find_transcript, read_active_model_from_transcript

        transcript = self._deployed_session(tmp_path, monkeypatch)

        found = _auto_find_transcript()

        assert found == str(transcript)
        assert read_active_model_from_transcript(found) == "claude-opus-5"

    def test_the_banner_names_the_model_actually_running(self, tmp_path, monkeypatch):
        """End to end: no `active_model` handed in, exactly as in task_start."""
        from model_routing import format_task_start_banner

        self._deployed_session(tmp_path, monkeypatch)

        out = format_task_start_banner(complexity="simple")

        assert "claude-opus-5" in out
        assert "unknown" not in out

    def test_a_mismatch_is_reachable_in_a_real_tree(self, tmp_path, monkeypatch):
        """The warning the banner exists for: a complex task on a model below
        the recommendation. Unreachable for as long as discovery was dead."""
        from model_routing import format_task_start_banner

        self._deployed_session(tmp_path, monkeypatch, model="claude-haiku-4-5-20251001")

        out = format_task_start_banner(complexity="complex")

        assert "MODEL MISMATCH" in out

    def test_without_a_transcript_the_banner_still_says_unknown(self, tmp_path, monkeypatch):
        """NEGATIVE: not finding a session stays 'unknown' — never a guess, and
        never an exception, because task_start runs through here."""
        import autoloop_presence as presence
        from model_routing import _auto_find_transcript, format_task_start_banner

        monkeypatch.setattr(
            presence.os.path, "expanduser", lambda p: p.replace("~", str(tmp_path), 1)
        )
        monkeypatch.setattr(presence, "project_slug", lambda _d: "D--nothing-here")
        monkeypatch.setattr("hooks.session_metrics.auto_find_transcript", lambda: None)
        monkeypatch.chdir(tmp_path)

        assert _auto_find_transcript() is None
        assert "unknown" in format_task_start_banner(complexity="simple")

    def test_the_fallback_finder_is_importable_as_a_package(self):
        """NEGATIVE / regression: `token_rows` is a sibling inside hooks/, so an
        import that puts only scripts/ on the path dies right here. That death
        was the whole defect, and it was invisible — swallowed into 'unknown'."""
        import importlib

        sm = importlib.import_module("hooks.session_metrics")

        assert callable(sm.auto_find_transcript)
        assert callable(sm.extract_token_rows)


class TestProfileSlugSingleSource:
    """model-registry-single-source — the id→slug map must be DERIVED from
    model_profiles, never hand-maintained, so a rank's canonical id and its
    reverse lookup can never drift.
    """

    def test_registry_is_the_reverse_of_default_families(self):
        # AC1: the derived map equals the reverse of DEFAULT_FAMILIES["claude"].
        import model_profiles
        from model_routing_matrix import _PROFILE_SLUG_BY_MODEL_ID

        expected = {
            model_profiles.normalize_model_id(spec["model"]): rank
            for rank, spec in model_profiles.DEFAULT_FAMILIES["claude"].items()
        }
        assert _PROFILE_SLUG_BY_MODEL_ID == expected

    def test_derivation_tracks_a_hypothetical_point_release_bump(self):
        # AC2 (drift guard): if a rank's canonical id changed in the source,
        # the derived reverse map follows — proving it is not a frozen literal.
        import model_profiles
        from model_routing_matrix import _derive_profile_slug_by_model_id

        bumped = {r: dict(s) for r, s in model_profiles.DEFAULT_FAMILIES["claude"].items()}
        original = model_profiles.DEFAULT_FAMILIES["claude"]
        model_profiles.DEFAULT_FAMILIES["claude"] = {
            **bumped,
            "opus": {"model": "claude-opus-4-9", "display": "Opus 4.9"},
        }
        try:
            derived = _derive_profile_slug_by_model_id()
            assert derived["claude-opus-4-9"] == "opus"
            assert "claude-opus-4-8" not in derived  # old id no longer a rank
        finally:
            model_profiles.DEFAULT_FAMILIES["claude"] = original

    @pytest.mark.parametrize(
        "model_id,expected_slug",
        [
            pytest.param("claude-haiku-4-5", "haiku", id="haiku"),
            pytest.param("claude-sonnet-4-6", "sonnet", id="sonnet"),
            pytest.param("claude-opus-4-8", "opus", id="opus_canonical"),
            pytest.param("claude-opus-4-7", "opus", id="opus_historical_via_family_fallback"),
            pytest.param("claude-opus-4-9", "opus", id="opus_future_point_release_via_fallback"),
            pytest.param("claude-fable-5", "fable", id="fable"),
            pytest.param("Claude-Opus-4-8[1m]", "opus", id="normalized_case_and_suffix"),
        ],
    )
    def test_slug_resolution_unchanged(self, model_id, expected_slug):
        # AC3: behaviour identical for every previously-mapped id, plus the
        # family-fallback cases the derived map deliberately omits.
        from model_routing_matrix import _model_id_to_profile_slug

        assert _model_id_to_profile_slug(model_id) == expected_slug

    @pytest.mark.parametrize(
        "model_id",
        [
            pytest.param("glm-4.6", id="non_claude_no_family_token"),
            pytest.param("mystery-9", id="unknown_id"),
            pytest.param("", id="empty"),
            pytest.param(None, id="none"),
        ],
    )
    def test_unrecognised_id_returns_none(self, model_id):
        # AC7 NEGATIVE: no family token and not in the registry → None, never a
        # silent wrong-slug guess.
        from model_routing_matrix import _model_id_to_profile_slug

        assert _model_id_to_profile_slug(model_id) is None
