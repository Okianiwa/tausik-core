"""Гейт судит только файлы СВОЕГО проекта, а вынесенные называет вслух.

Происхождение регрессии: портфельная схема (учёт задач здесь, код в другом
репозитории) законно кладёт внешние пути в relevant_files, и `task done` вставал
на filesize по чужому файлу — сперва на тестовом файле в 479 строк из репозитория,
чей контракт тесты от лимита освобождает, неделей позже на скрипте опс-слоя в 706
строк. Правило 400/500 применялось к файлу, чей проект его не принимал.

Два требования разом, и второе важнее первого: чужие файлы должны выпасть из
проверки И при этом быть названными. Наивная фильтрация превращает блок в тихую
зелень — замерено, что filesize на пустом списке отвечает «All files within line
limit» со skipped=False, то есть настоящим PASS при нулевой проверке.
"""

from __future__ import annotations

import os
import sys

import pytest

_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import gate_scope  # noqa: E402
import service_verification as sv  # noqa: E402
from conftest import VERIFICATION_RUNS_DDL  # noqa: E402
from gate_runner import format_results, run_gates  # noqa: E402
from gate_registry import judges_scope  # noqa: E402
from gate_scope import external_scope_note, split_by_project_root  # noqa: E402

_FILESIZE_ONLY = [{"name": "filesize", "severity": "block", "max_lines": 400}]


def _only_filesize(monkeypatch):
    """Свести набор гейтов к filesize: утверждения тут про скоуп, а не про конфиг."""
    monkeypatch.setattr("gate_runner.get_gates_for_trigger", lambda trigger, cfg: _FILESIZE_ONLY)
    monkeypatch.setattr("gate_runner.load_config", lambda: {})


def _oversized(path, lines=450):
    path.write_text("x\n" * lines, encoding="utf-8")
    return str(path)


class TestSplitByProjectRoot:
    def test_absolute_outside_is_dropped(self, tmp_path, monkeypatch):
        root = tmp_path / "proj"
        root.mkdir()
        monkeypatch.setattr(gate_scope, "project_root", lambda: str(root))

        inside, outside = split_by_project_root([str(tmp_path / "other" / "a.py")])

        assert inside == []
        assert outside == [str(tmp_path / "other" / "a.py")]

    def test_absolute_inside_is_kept(self, tmp_path, monkeypatch):
        root = tmp_path / "proj"
        root.mkdir()
        monkeypatch.setattr(gate_scope, "project_root", lambda: str(root))
        target = str(root / "scripts" / "a.py")

        inside, outside = split_by_project_root([target])

        assert inside == [target]
        assert outside == []

    def test_relative_paths_are_never_foreign(self, tmp_path, monkeypatch):
        """Несущее, а не сокращение: pre-commit гоняет гейты с cwd=временное дерево
        staged-содержимого, а конфиг резолвит через TAUSIK_DIR обратно в чекаут.
        Резолв относительного пути от cwd вынес бы туда весь коммит — гейты
        отчитались бы зелёным, не проверив ни строки."""
        root = tmp_path / "proj"
        root.mkdir()
        monkeypatch.setattr(gate_scope, "project_root", lambda: str(root))
        monkeypatch.chdir(tmp_path)

        inside, outside = split_by_project_root(["scripts/a.py", "docs/b.md"])

        assert inside == ["scripts/a.py", "docs/b.md"]
        assert outside == []

    def test_unresolvable_root_keeps_everything_in_scope(self, monkeypatch):
        """Fail-closed: страж, не знающий, где он, не может быть причиной, по
        которой проверка перестала выполняться."""
        monkeypatch.setattr(gate_scope, "project_root", lambda: None)

        inside, outside = split_by_project_root(["D:/elsewhere/a.py", "scripts/b.py"])

        assert inside == ["D:/elsewhere/a.py", "scripts/b.py"]
        assert outside == []

    def test_sibling_directory_sharing_a_prefix_is_foreign(self, tmp_path, monkeypatch):
        """`/x/proj-tools` не должен читаться как «внутри /x/proj»."""
        root = tmp_path / "proj"
        root.mkdir()
        monkeypatch.setattr(gate_scope, "project_root", lambda: str(root))
        sibling = str(tmp_path / "proj-tools" / "a.py")

        inside, outside = split_by_project_root([sibling])

        assert inside == []
        assert outside == [sibling]

    def test_a_root_that_does_not_exist_is_not_a_root(self, tmp_path, monkeypatch):
        """`find_tausik_dir` возвращает cwd/.tausik независимо от того, есть ли
        такой каталог. Корень, выдуманный из текущей директории процесса, — ровно
        неверный ответ для стража, решающего, какие файлы перестают проверяться."""
        monkeypatch.setenv("TAUSIK_DIR", str(tmp_path / "nowhere" / ".tausik"))

        assert gate_scope.project_root() is None


class TestRunGatesScope:
    def test_foreign_oversized_file_does_not_block(self, tmp_path, monkeypatch):
        _only_filesize(monkeypatch)
        root = tmp_path / "proj"
        root.mkdir()
        foreign_dir = tmp_path / "other"
        foreign_dir.mkdir()
        foreign = _oversized(foreign_dir / "bench.py")
        monkeypatch.setattr(gate_scope, "project_root", lambda: str(root))

        passed, results = run_gates("task-done", [foreign])

        assert passed is True
        fs = next(r for r in results if r["name"] == "filesize")
        assert fs["skipped"] is True, "опустевший скоуп обязан сказать «проверено ничего»"
        assert "NOT CHECKED HERE" in fs["output"]
        assert "bench.py" in fs["output"]

    def test_own_oversized_file_still_blocks(self, tmp_path, monkeypatch):
        """Контроль мутации: без него фикс неотличим от выключенного гейта."""
        _only_filesize(monkeypatch)
        root = tmp_path / "proj"
        root.mkdir()
        own = _oversized(root / "big.py")
        monkeypatch.setattr(gate_scope, "project_root", lambda: str(root))

        passed, results = run_gates("task-done", [own])

        assert passed is False
        fs = next(r for r in results if r["name"] == "filesize")
        assert fs["passed"] is False
        assert "450 lines" in fs["output"]

    def test_mixed_scope_judges_own_and_drops_foreign(self, tmp_path, monkeypatch):
        _only_filesize(monkeypatch)
        root = tmp_path / "proj"
        root.mkdir()
        foreign_dir = tmp_path / "other"
        foreign_dir.mkdir()
        own = _oversized(root / "big.py")
        foreign = _oversized(foreign_dir / "bench.py", lines=479)
        monkeypatch.setattr(gate_scope, "project_root", lambda: str(root))

        passed, results = run_gates("task-done", [own, foreign])

        assert passed is False
        fs = next(r for r in results if r["name"] == "filesize")
        assert "big.py" in fs["output"]
        assert "bench.py" not in fs["output"], "чужой файл не может стоять в вердикте"
        assert "bench.py" in fs["scope_note"], "но обязан стоять в том, что НЕ проверено"

    def test_unresolvable_root_still_blocks_foreign_file(self, tmp_path, monkeypatch):
        """Fail-closed от края до края: нет корня — прежнее поведение, а не пропуск."""
        _only_filesize(monkeypatch)
        foreign = _oversized(tmp_path / "bench.py")
        monkeypatch.setattr(gate_scope, "project_root", lambda: None)

        passed, _ = run_gates("task-done", [foreign])

        assert passed is False


class TestOnlyScopeJudgingGatesAreMuted:
    """Пустой скоуп молчит не у всех гейтов, а у тех, чей вердикт СЧИТАЕТСЯ из
    списка. Гейт, который список игнорирует (class_surface, memory_route,
    state_roundtrip), проверяет одно и то же в обоих случаях, и подменить его
    вердикт на «проверено ничего» значило бы соврать в другую сторону."""

    def test_the_registry_names_the_list_judging_gates(self):
        assert judges_scope("filesize") is True
        assert judges_scope("tdd_order") is True
        assert judges_scope("skill_spec_conformance") is True

    def test_a_gate_that_ignores_files_is_not_muted(self):
        assert judges_scope("class_surface") is False
        assert judges_scope("memory_route") is False
        assert judges_scope("state_roundtrip") is False

    def test_an_unknown_gate_is_not_muted(self):
        """Стековый или пользовательский гейт — командный по построению, и на
        пустой скоуп он отвечает своим сентинелом, а не голым PASS."""
        assert judges_scope("hadolint") is False
        assert judges_scope("не-существует") is False

    def test_a_file_ignoring_gate_still_runs_on_an_emptied_scope(self, tmp_path, monkeypatch):
        """Проверка на поведении, а не на флаге: гейт, игнорирующий список,
        обязан выполниться и вернуть свой настоящий вердикт."""
        monkeypatch.setattr(
            "gate_runner.get_gates_for_trigger",
            lambda trigger, cfg: [{"name": "state_roundtrip", "severity": "block"}],
        )
        monkeypatch.setattr("gate_runner.load_config", lambda: {})
        root = tmp_path / "proj"
        root.mkdir()
        monkeypatch.setattr(gate_scope, "project_root", lambda: str(root))
        ran = []
        monkeypatch.setattr(
            "gate_runner.impl_for",
            lambda name: lambda gate, files: ran.append(files) or (True, "проверено"),
        )

        _, results = run_gates("task-done", [str(tmp_path / "other" / "a.py")])

        assert ran == [[]], "гейт вызван, пусть и с пустым списком — он его не смотрит"
        assert results[0]["output"] == "проверено"
        assert results[0].get("skipped") is not True


class TestScopeNoteIsRendered:
    """Вердикт проверяется на РЕНДЕРЕ, а не на возвращённом словаре: строка,
    которую никто не печатает, ничего не сообщает человеку."""

    def test_note_prints_next_to_a_passing_gate(self, tmp_path, monkeypatch):
        _only_filesize(monkeypatch)
        root = tmp_path / "proj"
        root.mkdir()
        foreign_dir = tmp_path / "other"
        foreign_dir.mkdir()
        (root / "small.py").write_text("x\n" * 10, encoding="utf-8")
        foreign = _oversized(foreign_dir / "bench.py")
        monkeypatch.setattr(gate_scope, "project_root", lambda: str(root))

        _, results = run_gates("task-done", [str(root / "small.py"), foreign])
        rendered = format_results(results)

        assert "[PASS] filesize" in rendered
        assert "NOT checked here" in rendered
        assert "bench.py" in rendered

    def test_an_emptied_scope_is_rendered_too(self, tmp_path, monkeypatch):
        """Строка, которая при опустевшем скоупе живёт в output пропущенного
        гейта: рендер печатает output только у упавших, так что без отдельной
        ветки человек увидел бы голый [SKIP] без причины."""
        _only_filesize(monkeypatch)
        root = tmp_path / "proj"
        root.mkdir()
        foreign_dir = tmp_path / "other"
        foreign_dir.mkdir()
        foreign = _oversized(foreign_dir / "bench.py")
        monkeypatch.setattr(gate_scope, "project_root", lambda: str(root))

        _, results = run_gates("task-done", [foreign])
        rendered = format_results(results)

        assert "[SKIP] filesize" in rendered, "PASS рядом с невзятыми файлами заявляет покрытие"
        assert "verified NOTHING" in rendered
        assert rendered.count("bench.py") == 1, "причина названа один раз, не дважды"

    def test_clean_scope_renders_no_note(self, tmp_path, monkeypatch):
        _only_filesize(monkeypatch)
        root = tmp_path / "proj"
        root.mkdir()
        (root / "small.py").write_text("x\n" * 10, encoding="utf-8")
        monkeypatch.setattr(gate_scope, "project_root", lambda: str(root))

        _, results = run_gates("task-done", [str(root / "small.py")])
        rendered = format_results(results)

        assert "SCOPE:" not in rendered
        assert "NOT CHECKED HERE" not in rendered


class TestExternalScopeNote:
    def test_emptied_scope_says_nothing_was_verified(self):
        note = external_scope_note(["/a/b.py"], scope_emptied=True)

        assert "verified NOTHING" in note
        assert "not a passing check" in note

    def test_partial_scope_says_what_was_left_out(self):
        note = external_scope_note(["/a/b.py"], scope_emptied=False)

        assert "NOT checked here" in note
        assert "/a/b.py" in note


@pytest.fixture
def conn(tmp_path):
    import sqlite3

    from backend_schema_gate_runs import GATE_RUNS_SQL

    c = sqlite3.connect(str(tmp_path / "t.db"))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(VERIFICATION_RUNS_DDL + ";")
    c.executescript(GATE_RUNS_SQL)
    c.commit()
    yield c
    c.close()


class TestForeignScopeIsNotMissingTests:
    """Скоуп целиком из чужих файлов — другой отказ, чем «исходник без теста», и
    отвечать на него тем же советом нельзя: «добавь tests/test_<basename>.py»
    неисполним для файла, которым проект не владеет (конвенция #129 — совет
    проверяется исполнением его текста). Блокировка здесь оставила бы портфельную
    задачу незакрываемой никаким доступным действием."""

    @staticmethod
    def _all_skipped(*_a, **_kw):
        return True, [{"name": "filesize", "passed": True, "skipped": True, "output": "skip"}]

    def test_foreign_only_scope_does_not_block_on_missing_tests(self, conn, monkeypatch):
        import gate_runner

        monkeypatch.setattr(gate_runner, "run_gates", self._all_skipped)
        monkeypatch.setattr(gate_scope, "project_root", lambda: os.path.join("D:", os.sep, "proj"))
        notes = []

        passed, results, status = sv.run_gates_with_cache(
            conn,
            "portfolio-task",
            [os.path.join("D:", os.sep, "other", "bench.py")],
            trigger="verify",
            append_notes_fn=lambda _s, m: notes.append(m),
        )

        assert passed is True
        assert status == "foreign-scope"
        assert not any(r["name"] == "scoped-pytest" for r in results), (
            "нельзя заявлять «нет тестов» для файла, которым проект не владеет"
        )
        assert any("NOT VERIFIED HERE" in n for n in notes), "пробел обязан быть назван вслух"

    def test_the_foreign_close_leaves_a_row(self, conn, monkeypatch):
        """Вердикт, который РАЗРЕШАЕТ закрытие, обязан оставить след (решение
        #146): за ним не стоит ни один выполненный гейт, и строка с
        no_tests_declared=1 — единственное, чем такое закрытие потом считается."""
        import gate_runner

        monkeypatch.setattr(gate_runner, "run_gates", self._all_skipped)
        monkeypatch.setattr(gate_scope, "project_root", lambda: os.path.join("D:", os.sep, "proj"))

        sv.run_gates_with_cache(
            conn,
            "portfolio-task",
            [os.path.join("D:", os.sep, "other", "bench.py")],
            trigger="verify",
        )

        rows = conn.execute(
            "SELECT task_slug, exit_code, no_tests_declared FROM verification_runs"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["task_slug"] == "portfolio-task"
        assert rows[0]["exit_code"] == 0
        assert rows[0]["no_tests_declared"] == 1

    def test_own_file_without_tests_still_blocks(self, conn, monkeypatch):
        """Контроль мутации: прежний страж обязан пережить новую ветку."""
        import gate_runner

        monkeypatch.setattr(gate_runner, "run_gates", self._all_skipped)
        monkeypatch.setattr(gate_scope, "project_root", lambda: os.path.join("D:", os.sep, "proj"))

        passed, results, status = sv.run_gates_with_cache(
            conn,
            "own-task",
            [os.path.join("D:", os.sep, "proj", "scripts", "foo.py")],
            trigger="verify",
        )

        assert passed is False
        assert status == "no-test-mapped"
        assert any(r["name"] == "scoped-pytest" for r in results)

    def test_mixed_scope_keeps_the_missing_test_verdict(self, conn, monkeypatch):
        """Один свой файл среди чужих — это по-прежнему непроверенный свой файл."""
        import gate_runner

        monkeypatch.setattr(gate_runner, "run_gates", self._all_skipped)
        monkeypatch.setattr(gate_scope, "project_root", lambda: os.path.join("D:", os.sep, "proj"))

        passed, _, status = sv.run_gates_with_cache(
            conn,
            "mixed-task",
            [
                os.path.join("D:", os.sep, "proj", "scripts", "foo.py"),
                os.path.join("D:", os.sep, "other", "bench.py"),
            ],
            trigger="verify",
        )

        assert passed is False
        assert status == "no-test-mapped"
