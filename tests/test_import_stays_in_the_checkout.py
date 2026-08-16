"""handoff-sys-path-escapes-to-profile — the suite must test the checkout.

`autoloop_handoff` computed the project root by counting three parents and
appended `.claude/scripts` to it. That is right for the DEPLOYED copy, which
lives at `<project>/.claude/scripts/`. In the hub the same file sits one level
shallower, in `<hub>/scripts/`, so three parents overshoot past the checkout
into the user's home — and `~/.claude/scripts` went onto `sys.path` at IMPORT
time, for the whole process.

Everything imported afterwards then came from the profile. Measured during a
full run of this directory: `autoloop_overlay.__file__` resolved under
`~/.claude`, and the profile entry appeared in `sys.path` twice. A suite in
that state is green about code that is not the source — the failure only
surfaced because the profile happened to be a couple of hours stale and a newly
added symbol was missing from it. Had the two copies agreed, nothing would have
shown.

It is an execution surface as well: any `autoloop_journal.py` placed in the
substituted directory would be imported and run.

It lives here rather than in `tests/autoloop_tests/`: that directory's
conftest stubs `subprocess.Popen` autouse (it once left 58 tkinter windows
on the desktop), and this sensor needs a real child process to mean
anything.

The sensor checks the BOUNDARY of the install tree rather than the name
`~/.claude`. A future miscalculation will land somewhere else, and a test that
knows only the old destination would wave it through.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

INSTALL_ROOT = Path(__file__).resolve().parents[1]

# Run in a CHILD process on purpose: in this one the modules are already in
# `sys.modules` from collection, so an in-process check would measure nothing
# and pass for the wrong reason.
_PROBE = r"""
import json, sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "scripts"))
before = list(sys.path)

import autoloop_handoff

added = [p for p in sys.path if p not in before]
def inside(p):
    try:
        return str(Path(p).resolve()).startswith(str(root))
    except OSError:
        return False

print(json.dumps({
    "added": added,
    "outside": [p for p in added if not inside(p)],
    "journal": autoloop_handoff.journal.__file__,
}))
"""


def _probe() -> dict:
    out = subprocess.run(
        [sys.executable, "-c", _PROBE, str(INSTALL_ROOT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_importing_handoff_adds_no_path_outside_the_checkout() -> None:
    """AC-1 and the sensor: the boundary, not a blocklist of one directory."""
    result = _probe()

    assert result["outside"] == [], (
        "import put a path outside the install tree on sys.path — every module "
        f"imported afterwards may come from there instead of the checkout: {result['outside']}"
    )


def test_the_journal_comes_from_the_checkout() -> None:
    """AC-2: the point of the boundary. A path that escapes is only a problem
    because of what gets imported through it, so assert the destination too."""
    journal = Path(_probe()["journal"]).resolve()

    assert str(journal).startswith(str(INSTALL_ROOT)), journal


def test_the_sibling_is_found_without_counting_parents(tmp_path: Path) -> None:
    """AC-3, negative: the deployed layout must keep working. `autoloop_journal`
    sits beside `autoloop_handoff` in BOTH layouts — hub `scripts/` and project
    `.claude/scripts/` — which is why the fix leans on adjacency instead of
    arithmetic. Copying the pair into a project-shaped tree proves the claim
    rather than asserting it."""
    deployed = tmp_path / "proj" / ".claude" / "scripts"
    deployed.mkdir(parents=True)
    src = INSTALL_ROOT / "scripts"
    for name in ("autoloop_handoff.py", "autoloop_journal.py"):
        (deployed / name).write_bytes((src / name).read_bytes())

    out = subprocess.run(
        [sys.executable, "-c", "import autoloop_handoff as h; print(h.journal.__file__)"],
        cwd=str(deployed),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env={"PYTHONPATH": str(deployed), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")},
    )

    assert out.returncode == 0, out.stderr
    assert str(deployed) in out.stdout


def test_running_as_a_script_still_works() -> None:
    """AC-4, negative: invoked as a script, Python puts the file's directory on
    `sys.path` itself. The fix must neither depend on that nor break it."""
    out = subprocess.run(
        [sys.executable, str(INSTALL_ROOT / "scripts" / "autoloop_handoff.py"), "show"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )

    assert "Traceback" not in (out.stderr or ""), out.stderr
