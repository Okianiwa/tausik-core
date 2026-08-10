"""SENAR Rule 5 - structured AC evidence parser (v1.4)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from service_ac_evidence import (  # noqa: E402
    build_report,
    parse_ac_text,
    parse_evidence_lines,
)


def test_parse_numbered_ac():
    ac = """
    1. Migration v21 creates table reviews
    2. backend_crud exposes review_record
    3. CLI works end-to-end
    """
    items = parse_ac_text(ac)
    assert len(items) == 3
    assert "Migration" in items[0]


def test_parse_evidence_with_test_ref():
    notes = "AC-1: ✓ tested via tests/test_foo.py::test_bar"
    lines = parse_evidence_lines(notes)
    assert len(lines) == 1
    e = lines[0]
    assert e.ac_index == 1
    assert e.has_checkmark is True
    assert "tests/test_foo.py::test_bar" in e.test_refs
    assert e.evidence_type == "test_ref"


def test_parse_evidence_manual():
    notes = "AC-2: ✓ manual run produced expected output"
    lines = parse_evidence_lines(notes)
    assert lines[0].ac_index == 2
    assert lines[0].is_manual is True
    assert lines[0].evidence_type == "test_ref" or lines[0].evidence_type == "manual"


def test_parse_evidence_negative_scenario():
    notes = "Negative: empty input returns 400 (manual curl run)"
    lines = parse_evidence_lines(notes)
    assert lines[0].is_negative is True


def test_match_evidence_full_coverage():
    ac = "1. add foo\n2. add bar\n3. add baz"
    notes = (
        "AC-1: ✓ tested via tests/test_x.py::test_a\n"
        "AC-2: ✓ tested via tests/test_x.py::test_b\n"
        "AC-3: ✓ manual smoke run\n"
        "Negative: empty payload returns 400"
    )
    rep = build_report(ac, notes)
    assert rep.total_ac == 3
    assert rep.covered == 3
    assert rep.coverage_pct == 100.0
    assert rep.has_negative_evidence is True
    assert rep.gaps() == []


def test_match_evidence_partial_coverage_finds_gaps():
    ac = "1. a\n2. b\n3. c"
    notes = "AC-1: ✓ tested via tests/test_x.py::test_a"
    rep = build_report(ac, notes)
    assert rep.covered == 1
    assert rep.gaps() == [2, 3]


def test_match_evidence_unmatched_lines_collected():
    ac = "1. a"
    notes = "Reviewed code, no specific AC tag"
    rep = build_report(ac, notes)
    assert rep.covered == 0
    assert rep.gaps() == [1]
    assert len(rep.unmatched_evidence) == 0  # plain text without keywords ignored


def test_inline_ac_reference_matches():
    ac = "1. a\n2. b"
    notes = "All good - ✓ checked AC-2 via tests/test_y.py"
    rep = build_report(ac, notes)
    item2 = next(i for i in rep.items if i.ac_index == 2)
    assert item2.has_test_ref is True


def test_parser_handles_empty_input():
    rep = build_report("", "")
    assert rep.total_ac == 0
    assert rep.covered == 0
    assert rep.coverage_pct == 0.0


def test_summary_shape():
    ac = "1. a\n2. b"
    notes = "AC-1: ✓ tested via tests/test_x.py"
    rep = build_report(ac, notes)
    s = rep.to_summary()
    assert "AC coverage" in s
    assert "gaps" in s
    assert "negative scenario" in s


# --- inline "AC verified: 1. … ✓ 2. …" journal format ------------------
# (ac-evidence-parser-misses-checkmark-format): a real closing produced
# "0/1 criteria with explicit evidence" although the journal held a full
# per-criterion breakdown — the whole breakdown lived in ONE journal line
# and the AC itself was one line with inline numbering.

# Faithful excerpt of the session #42 journal line that measured the defect.
_REAL_JOURNAL_LINE = (
    "[2026-08-10T20:56:20Z] AC verified: "
    "1. Единая реализация ✓ — оба пути зовут service_claudemd.build_dynamic_content "
    "(тест test_cli_and_mcp_use_the_same_builder ловит через sentinel-monkeypatch: "
    "2 вызова, контент в файле на обоих путях). "
    "2. Тест на стирание ✓ — test_mcp_update_claudemd_preserves_memory_tail; "
    "замер на до-фиксовом коде (git show HEAD): OLD=TAIL ERASED, NEW=TAIL PRESERVED. "
    "3. Живой рендер ✓ — развёрнутая копия в свежем интерпретаторе записала "
    "D:/ModLoader/CLAUDE.md С секцией '### Memory tail' (проверено чтением файла); "
    "контроль паритета: CLI -> 'already up-to-date' (контент побайтно идентичен). "
    "4. Негатив ✓ — пустая память: нет секции-огрызка "
    "(test_build_dynamic_content_empty_memory_no_tail_stub); отказ генерации: файл "
    "обновлён + 'Warning: memory tail unavailable' в ответе обработчика."
)

_REAL_AC_ONE_LINE = (
    "1. Единая реализация: MCP-обработчик вызывает ту же функцию записи "
    "динамической секции, что и CLI-путь. 2. Регрессионный тест ловит именно "
    "СТИРАНИЕ: секция обязана сохраниться. 3. Живой замер на рендере: секция "
    "остаётся в файле. 4. Негативный сценарий: при пустой памяти обновление "
    "НЕ падает и не оставляет пустой секции."
)


def test_parse_ac_text_inline_single_line_counts_all():
    items = parse_ac_text(_REAL_AC_ONE_LINE)
    assert len(items) == 4
    assert "Единая реализация" in items[0]
    assert "Негативный сценарий" in items[3]


def test_real_journal_line_session42_full_coverage():
    rep = build_report(_REAL_AC_ONE_LINE, _REAL_JOURNAL_LINE)
    assert rep.total_ac == 4
    assert rep.covered == 4, f"gaps: {rep.gaps()}"
    assert rep.gaps() == []
    assert rep.has_negative_evidence is True  # «Негатив» — кириллица распознана


def test_inline_items_carry_their_own_test_refs():
    ac = "1. a 2. b"
    notes = "AC verified: 1. ✓ tests/test_a.py::test_x 2. ✓ manual smoke run"
    rep = build_report(ac, notes)
    item1 = next(i for i in rep.items if i.ac_index == 1)
    item2 = next(i for i in rep.items if i.ac_index == 2)
    assert item1.has_test_ref is True
    assert item1.has_manual is False  # сигналы посегментные, не на всю строку
    assert item2.has_manual is True
    assert item2.has_test_ref is False


def test_ac_verified_prose_without_numbers_earns_nothing():
    rep = build_report(
        "1. a\n2. b",
        "[2026-08-10T21:00:00Z] AC verified: всё хорошо ✓ критерии сходятся",
    )
    assert rep.covered == 0
    assert rep.gaps() == [1, 2]


def test_empty_notes_keep_full_gaps():
    rep = build_report("1. a 2. b", "")
    assert rep.total_ac == 2
    assert rep.covered == 0
    assert rep.gaps() == [1, 2]


# --- numbers quoted inside «…» are not criteria -------------------------
# (ac-splitter-counts-quoted-numbers): the very closing of the parser fix
# rendered "4/7 (gaps: AC 5, 6, 7)" — the splitter cut on the numbered
# format examples QUOTED inside criterion bodies.

# Faithful excerpt of the AC text that measured the defect.
_QUOTED_AC_REAL = (
    "1. Парсер распознаёт фактический журнальный формат "
    "«AC verified: 1. … ✓ … 2. … ✓ …» (нумерованный список после префикса "
    "AC verified) и засчитывает evidence по каждому номеру критерия: NOTE "
    "рапортует N/N, а не 0/N. "
    "2. Прежний формат «AC-1: ✓ tested via tests/...» продолжает "
    "распознаваться, регресс запрещён. "
    "3. Тест воспроизводит замеренный случай: реальная строка журнала "
    "(сессия #42) даёт полное покрытие критериев. "
    "4. Негативный сценарий: произвольный текст не засчитывается."
)


def test_numbers_inside_guillemets_are_not_criteria():
    items = parse_ac_text(_QUOTED_AC_REAL)
    assert len(items) == 4, [i[:40] for i in items]
    assert "Парсер распознаёт" in items[0]
    assert "Негативный сценарий" in items[3]


def test_quoted_example_in_journal_marker_line_not_split():
    ac = "1. a 2. b"
    notes = "AC verified: 1. ✓ формат «AC verified: 1. … 2. …» распознан 2. ✓ ok"
    rep = build_report(ac, notes)
    assert rep.total_ac == 2
    assert rep.covered == 2
    assert rep.gaps() == []


def test_unpaired_closing_guillemet_clamps_depth():
    text = "странный » хвост 1. a 2. b «пример 7. внутри» 3. c"
    items = parse_ac_text(text)
    assert len(items) == 3, [i[:40] for i in items]
    assert "внутри" in items[1]  # «7.» остался телом второго критерия
    assert items[2] == "c"


def test_partial_verification_journal_starts_mid_list():
    ac = "1. a 2. b 3. c"
    notes = "AC verified: 3. ✓ добит последний критерий tests/test_x.py::test_c"
    rep = build_report(ac, notes)
    item3 = next(i for i in rep.items if i.ac_index == 3)
    assert item3.has_any_evidence is True
    assert rep.covered == 1


def test_large_number_in_prose_does_not_open_a_list():
    ac = "1. проверка (сессия #42) даёт полное покрытие 2. негатив"
    items = parse_ac_text(ac)
    assert len(items) == 2, [i[:40] for i in items]
