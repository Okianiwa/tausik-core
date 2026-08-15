"""Обёртки CLI: что доезжает до аргумента, а что теряется по дороге.

Найдено в бою: за одну сессию через `.tausik/tausik` (то есть `tausik.cmd`)
записаны шесть заметок памяти и десяток строк доказательств — и каждая легла в
базу первой строкой. Ни одна команда не отказала: cmd.exe заканчивает разбор
командной строки на переводе строки, поэтому остальное просто не доехало, и
вызывающий не мог этого заметить.

Поэтому здесь проверяется не только фикс, но и сам дефект: пока cmd-обёртка
существует, её ограничение должно быть записано тестом, а не памятью человека.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason=".cmd и .ps1 — только Windows")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BOOTSTRAP = os.path.join(_REPO, "bootstrap")

MULTILINE = "AC-1: первая строка\nAC-2: вторая строка"
SINGLE = "AC-1: одна строка"


@pytest.fixture
def wrappers(tmp_path):
    """Развёрнутый проект, где вместо project.py стоит эхо аргументов."""
    scripts = tmp_path / ".claude" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "project.py").write_text(
        "import json, sys\nprint(json.dumps(sys.argv[1:], ensure_ascii=False))\n",
        encoding="utf-8",
    )
    tausik = tmp_path / ".tausik"
    tausik.mkdir()
    if _BOOTSTRAP not in sys.path:
        sys.path.insert(0, _BOOTSTRAP)
    from bootstrap_venv import install_cli_wrapper

    install_cli_wrapper(_BOOTSTRAP, str(tausik))
    return tausik


def _argv(command: list[str], cwd) -> list[str]:
    r = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(cwd)
    )
    assert r.stdout.strip(), f"обёртка ничего не напечатала: {r.stderr[:200]}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def _via_powershell(tausik, arg, cwd):
    return _argv(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(tausik / "tausik.ps1"),
            "log",
            arg,
        ],
        cwd,
    )


def _via_cmd(tausik, arg, cwd):
    return _argv(["cmd", "/c", str(tausik / "tausik.cmd"), "log", arg], cwd)


def test_the_powershell_wrapper_keeps_a_multi_line_argument(wrappers, tmp_path):
    """Строка доказательств, заметка памяти, лог задачи — всё это многострочное,
    и через штатный вызов оно обязано доезжать целиком."""
    argv = _via_powershell(wrappers, MULTILINE, tmp_path)

    assert argv[-1] == MULTILINE
    assert "\n" in argv[-1]
    assert "AC-2" in argv[-1]


def test_the_cmd_wrapper_is_the_one_that_truncates(wrappers, tmp_path):
    """Сам дефект, записанный как факт: cmd.exe обрывает аргумент на переводе
    строки. Тест не требует это чинить — он требует, чтобы ограничение было
    известно, пока обёртка существует. Если однажды cmd начнёт доносить текст
    целиком, тест упадёт и об этом скажет."""
    argv = _via_cmd(wrappers, MULTILINE, tmp_path)

    assert argv[-1] == "AC-1: первая строка"
    assert "AC-2" not in argv[-1]


def test_a_single_line_argument_survives_both_wrappers(wrappers, tmp_path):
    """NEGATIVE: обычные вызовы не должны измениться ни в одной обёртке —
    иначе фикс многострочности стал бы регрессом для всего остального."""
    assert _via_powershell(wrappers, SINGLE, tmp_path)[-1] == SINGLE
    assert _via_cmd(wrappers, SINGLE, tmp_path)[-1] == SINGLE


def test_bootstrap_ships_all_three_wrappers(wrappers):
    """NEGATIVE: bash-путь (CI, git bash) не должен исчезнуть из-за появления
    PowerShell-обёртки — три файла, три способа вызова."""
    for name in ("tausik", "tausik.cmd", "tausik.ps1"):
        assert (wrappers / name).is_file(), name
