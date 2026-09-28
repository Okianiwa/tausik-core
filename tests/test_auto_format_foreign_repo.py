"""auto_format leaves files of another repository alone.

An agent often has a sibling repository open next to the TAUSIK project. The
hook formatted whatever file was edited, so a 20-line edit in a sibling repo
that keeps its own style came back as a 240-line reformat, and the edit was
logged into THIS project's active task as `Modified: ../../other/...`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

_HOOK = os.path.join(os.path.dirname(__file__), "..", "scripts", "hooks", "auto_format.py")
_UNFORMATTED = "x=1\ny =  [1,2]\n"


def _run(file_path, project_dir):
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)}
    env.pop("TAUSIK_SKIP_HOOKS", None)
    return subprocess.run(
        [sys.executable, _HOOK],
        input=json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(file_path)}}),
        text=True,
        encoding="utf-8",
        capture_output=True,
        env=env,
        timeout=30,
    )


def test_file_of_a_sibling_repository_is_not_formatted(tmp_path):
    proj, other = tmp_path / "proj", tmp_path / "other"
    proj.mkdir()
    other.mkdir()
    target = other / "mod.py"
    target.write_text(_UNFORMATTED, encoding="utf-8")
    assert _run(target, proj).returncode == 0
    assert target.read_text(encoding="utf-8") == _UNFORMATTED


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff is not installed")
def test_file_of_the_project_is_still_formatted(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    target = proj / "mod.py"
    target.write_text(_UNFORMATTED, encoding="utf-8")
    assert _run(target, proj).returncode == 0
    assert target.read_text(encoding="utf-8") != _UNFORMATTED
