"""Behavioral tests for scripts/gate_test_citation.py (gate-integrity-tests).

This is the Rule 5 anti-fabrication predicate: it decides whether a claimed AC
evidence citation (`tests/foo.py::test_bar`) names a REAL test. Its adversary is
an agent that can write anything into its own notes, so the whole value is in the
verdicts it REFUSES. It shipped with zero behavioral tests; these lock in the
four escapes its own code comments say earlier drafts had — invented `::name` on
a real file, `..` traversal out of the tree, a bare basename matching anything,
and a cwd-dependent root — so a regression re-opening any of them reddens CI.
"""

from __future__ import annotations

import json
import os
import sys

_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from ac_evidence_detectors import TEST_REF_RE  # noqa: E402
from gate_test_citation import (  # noqa: E402
    _code_roots,
    _named_test_defined,
    _project_root,
    _test_ref_exists,
)


def _make_project(tmp_path):
    """A minimal project tree: a real test file with a plain test, a class
    method, and an async test — plus a non-test source file to traverse toward."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text(
        "def test_alpha():\n"
        "    assert True\n"
        "\n"
        "class TestBeta:\n"
        "    def test_gamma(self):\n"
        "        assert True\n"
        "\n"
        "async def test_delta():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "impl.py").write_text("def something():\n    return 1\n", encoding="utf-8")
    return str(tmp_path)


# ------------------------------------------------------------ AC1: real vs invented file ---


class TestRefResolution:
    def test_real_file_and_function_resolves(self, tmp_path):
        root = _make_project(tmp_path)
        assert _test_ref_exists("tests/test_sample.py::test_alpha", root=root) is True

    def test_nonexistent_file_fails_closed(self, tmp_path):
        root = _make_project(tmp_path)
        assert _test_ref_exists("tests/does_not_exist.py::test_alpha", root=root) is False

    def test_file_only_citation_is_accepted(self, tmp_path):
        """Honest evidence often names a file with no ::function — allowed."""
        root = _make_project(tmp_path)
        assert _test_ref_exists("tests/test_sample.py", root=root) is True

    def test_empty_path_fails_closed(self, tmp_path):
        root = _make_project(tmp_path)
        assert _test_ref_exists("::test_alpha", root=root) is False
        assert _test_ref_exists("", root=root) is False


# ---------------------------------------------------- AC2: anti-fabrication escapes ---


class TestAntiFabrication:
    def test_invented_function_on_real_file_fails(self, tmp_path):
        """The core anti-fabrication case: real file, invented test name."""
        root = _make_project(tmp_path)
        assert _test_ref_exists("tests/test_sample.py::test_never_written", root=root) is False

    def test_class_method_node_id_resolves(self, tmp_path):
        root = _make_project(tmp_path)
        assert _test_ref_exists("tests/test_sample.py::TestBeta::test_gamma", root=root) is True

    def test_invented_method_under_real_class_fails(self, tmp_path):
        root = _make_project(tmp_path)
        assert _test_ref_exists("tests/test_sample.py::TestBeta::test_missing", root=root) is False

    def test_parameterised_id_strips_bracket(self, tmp_path):
        root = _make_project(tmp_path)
        assert _test_ref_exists("tests/test_sample.py::test_alpha[case-3]", root=root) is True

    def test_async_test_is_recognised(self, tmp_path):
        root = _make_project(tmp_path)
        assert _test_ref_exists("tests/test_sample.py::test_delta", root=root) is True

    def test_dotdot_traversal_out_of_tree_fails(self, tmp_path):
        """`tests/../scripts/impl.py` normalises out of tests/ — must not let the
        gate's own implementation tree count as a test."""
        root = _make_project(tmp_path)
        assert _test_ref_exists("tests/../scripts/impl.py::something", root=root) is False

    def test_bare_basename_resolves_only_inside_tests(self, tmp_path):
        root = _make_project(tmp_path)
        # present in tests/ → resolved
        assert _test_ref_exists("test_sample.py::test_alpha", root=root) is True
        # not present in tests/ → not invented from elsewhere in the tree
        assert _test_ref_exists("impl.py::something", root=root) is False


# ------------------------------------------------------ _named_test_defined unit ---


class TestNamedTestDefined:
    def test_no_name_segment_is_true(self, tmp_path):
        root = _make_project(tmp_path)
        path = os.path.join(root, "tests", "test_sample.py")
        assert _named_test_defined(path, "tests/test_sample.py") is True

    def test_unreadable_file_fails_closed(self, tmp_path):
        missing = os.path.join(str(tmp_path), "gone.py")
        assert _named_test_defined(missing, "gone.py::test_alpha") is False


# ------------------------------------------- sibling repository and PHP tests ---


def _make_sibling(tmp_path):
    """A planning project whose .mcp.json binds `serena-app` to a PHP repo with Pest and PHPUnit tests."""
    app = tmp_path / "app"
    (app / "tests" / "Feature").mkdir(parents=True)
    (app / "tests" / "Feature" / "LoginTest.php").write_text(
        "<?php\n"
        "it('asks for a code after a correct password', function () {});\n"
        'test("refuses a stale code", function () {});\n'
        "class LoginUnitTest extends TestCase {\n"
        "    public function test_locks_after_five_failures(): void {}\n"
        "}\n",
        encoding="utf-8",
    )
    (app / "src").mkdir()
    (app / "src" / "Login.php").write_text("<?php\nfunction handle() {}\n", encoding="utf-8")
    plan = tmp_path / "plan"
    plan.mkdir()
    (plan / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"serena-app": {"args": ["start-mcp-server", "--project", str(app)]}}}),
        encoding="utf-8",
    )
    return str(plan)


class TestSiblingRepository:
    def test_pest_description_in_sibling_resolves(self, tmp_path):
        root = _make_sibling(tmp_path)
        ref = "tests/Feature/LoginTest.php::\"asks for a code after a correct password\""
        assert _test_ref_exists(ref, root=root) is True
        assert _test_ref_exists("tests/Feature/LoginTest.php::'refuses a stale code'", root=root) is True

    def test_phpunit_method_resolves(self, tmp_path):
        root = _make_sibling(tmp_path)
        assert _test_ref_exists("tests/Feature/LoginTest.php::test_locks_after_five_failures", root=root) is True

    def test_invented_pest_description_fails(self, tmp_path):
        root = _make_sibling(tmp_path)
        assert _test_ref_exists("tests/Feature/LoginTest.php::\"logs in without a code\"", root=root) is False

    def test_traversal_out_of_sibling_tests_fails(self, tmp_path):
        root = _make_sibling(tmp_path)
        assert _test_ref_exists("tests/../src/Login.php::handle", root=root) is False

    def test_no_mcp_json_means_project_root_only(self, tmp_path):
        assert _code_roots(str(tmp_path)) == [str(tmp_path)]

    def test_plain_serena_and_other_servers_add_no_root(self, tmp_path):
        (tmp_path / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"serena": {"args": ["--project", str(tmp_path / "x")]}, "rag": {"args": []}}}),
            encoding="utf-8",
        )
        assert _code_roots(str(tmp_path)) == [str(tmp_path)]

    def test_parser_captures_quoted_php_citation(self):
        line = '✓ tests/Feature/LoginTest.php::"asks for a code after a correct password"; ✓ tests/x.py::test_a'
        assert TEST_REF_RE.findall(line) == [
            'tests/Feature/LoginTest.php::"asks for a code after a correct password"',
            "tests/x.py::test_a",
        ]


# ------------------------------------------------------------ _project_root ---


class TestProjectRoot:
    def test_explicit_root_wins(self, tmp_path):
        assert _project_root(str(tmp_path)) == str(tmp_path)

    def test_env_var_used_when_no_arg(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        assert _project_root() == str(tmp_path)
