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


def _bootstrap_dir() -> str:
    """Hub keeps the wrapper templates in `bootstrap/`; a deployed project has
    them under `.tausik-lib/bootstrap/`. The flat project copy of this file runs
    there, and the wrappers are exactly what it must be able to check."""
    for rel in (("bootstrap",), (".tausik-lib", "bootstrap")):
        candidate = os.path.join(_REPO, *rel)
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(_REPO, "bootstrap")


_BOOTSTRAP = _bootstrap_dir()

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


def _noisy(tmp_path, exit_code: int = 0):
    """Замена project.py, которая ведёт себя как настоящий CLI: печатает в оба
    потока (гейты пишут прогресс именно в stderr) и возвращает свой код."""
    script = tmp_path / ".claude" / "scripts" / "project.py"
    script.write_text(
        "import sys\n"
        "print('[gates] Running 7 gate(s)', file=sys.stderr)\n"
        "print('DONE-MARKER')\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )


def _run_ps(tausik, tmp_path, *args):
    """Запуск с `2>&1` ВНУТРИ PowerShell — именно так вызывает агентский
    harness, и именно эта форма меняет поведение 5.1: перенаправленный поток
    нативной команды заворачивается в ErrorRecord'ы. Родительское
    перенаправление (capture_output у subprocess) этого не делает, поэтому
    первая версия теста проходила и на сломанной обёртке."""
    quoted = " ".join(f"'{a}'" for a in args)
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"& '{tausik / 'tausik.ps1'}' {quoted} 2>&1; exit $LASTEXITCODE",
        ],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(tmp_path),
    )


def test_progress_on_stderr_does_not_abort_the_call(wrappers, tmp_path):
    """Дефект первой версии обёртки: под `$ErrorActionPreference='Stop'` первая
    же строка прогресса гейтов становилась терминальной ошибкой, вызов обрывался
    на середине — `task done` печатал гейты и не закрывал ничего."""
    _noisy(tmp_path)

    r = _run_ps(wrappers, tmp_path, "done", "slug")

    assert "DONE-MARKER" in r.stdout, r.stderr[:300]
    assert r.returncode == 0


def test_the_exit_code_is_passed_through(wrappers, tmp_path):
    """NEGATIVE: терпимость к stderr не должна превратиться в «всё хорошо» —
    отказ CLI обязан остаться отказом."""
    _noisy(tmp_path, exit_code=3)

    assert _run_ps(wrappers, tmp_path, "done", "slug").returncode == 3


def test_bootstrap_ships_all_three_wrappers(wrappers):
    """NEGATIVE: bash-путь (CI, git bash) не должен исчезнуть из-за появления
    PowerShell-обёртки — три файла, три способа вызова."""
    for name in ("tausik", "tausik.cmd", "tausik.ps1"):
        assert (wrappers / name).is_file(), name
