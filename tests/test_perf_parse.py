"""Нормировка долей /perf на тик (async-platform/mc/perf_parse.py).

Пинит ловушку memory #42, на которой строится весь отчёт mc-tick-profile.

В дереве /perf первая строка — `nextTickWait`, и это СОН сервера между тиками.
На незагруженном сервере он занимает 84-99% wall-времени. Колонка «% от общего»
считает от wall ВМЕСТЕ СО СНОМ, поэтому в ней любая подсистема на любой сцене
читается как «0.29%». Наивное чтение этой колонки даёт «блок-энтити ничего не
стоят» АВТОМАТИЧЕСКИ — то есть ложно ПОДТВЕРЖДАЕТ опровержение ставки A эпика,
не измерив ничего.

Правильная величина — доля от РАБОТЫ: subsystem_of_total / tick_of_total.

Числа в фикстуре — настоящие, из прогона сессии #9 (сцена «свежий выживач,
1 игрок стоит»), а не выдуманные.

Имя файла повторяет имя модуля: gate_test_resolver мапит scoped pytest по
basename (см. tests/test_backend_roadmap.py).
"""

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "async-platform" / "mc" / "perf_parse.py"

# Срез настоящего profiling.txt: шапка + ветка overworld. Отступы и формат
# «%_от_родителя/%_от_общего» сохранены дословно — парсер их и разбирает.
REAL_PROFILE = """---- Minecraft Profiler Results ----
// Now with the same numbers

Version: 26.2
Time span: 10041 ms
Tick span: 202 ticks
// This is approximately 20.12 ticks per second. It should be 20 ticks per second

--- BEGIN PROFILE DUMP ---

[00] nextTickWait(202/1) - 84.51%/84.51%
[00] tick(202/1) - 15.45%/15.45%
[01] |   levels(202/1) - 98.28%/15.19%
[02] |   |   ServerLevel[world] minecraft:overworld(202/1) - 99.08%/15.05%
[03] |   |   |   tick(202/1) - 99.99%/15.05%
[04] |   |   |   |   entities(202/1) - 55.55%/8.36%
[04] |   |   |   |   chunkSource(202/1) - 40.93%/6.16%
[04] |   |   |   |   blockEntities(202/1) - 1.90%/0.29%
[04] |   |   |   |   tickPending(202/1) - 1.21%/0.18%
--- END PROFILE DUMP ---

--- BEGIN COUNTER DUMP ---
[00] tick total:0/0 average: 0/0
--- END COUNTER DUMP ---
"""


@pytest.fixture(scope="module")
def pp():
    spec = importlib.util.spec_from_file_location("perf_parse_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    # dataclass резолвит аннотации через sys.modules[cls.__module__].
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    del sys.modules[spec.name]


@pytest.fixture
def profile_zip(tmp_path):
    """ZIP той же раскладки, что отдаёт /perf."""
    z = tmp_path / "2026-07-17_01_15_13-world-26_2.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("server/profiling.txt", REAL_PROFILE)
        for dim in ("overworld", "the_nether", "the_end"):
            base = f"server/levels/minecraft/{dim}"
            f.writestr(f"{base}/block_entities.csv", "x,y,z,type\n1,2,3,minecraft:chest\n")
            f.writestr(f"{base}/entities.csv", "x,y,z,uuid,type\n1,2,3,u,minecraft:cow\n")
            f.writestr(f"{base}/chunks.csv", "x,z\n0,0\n1,1\n")
    return z


def test_header_parsed(pp, profile_zip):
    p = pp.parse_zip(profile_zip)
    assert (p.ticks, p.span_ms, p.tps) == (202, 10041, 20.12)


def test_tick_of_total_excludes_sleep(pp, profile_zip):
    """`tick` — это РАБОТА; nextTickWait (84.51%) в неё не входит."""
    p = pp.parse_zip(profile_zip)
    assert p.tick_of_total == pytest.approx(15.45)


def test_share_is_normalized_to_tick_not_wall(pp, profile_zip):
    """Ядро memory #42: 0.29% от wall — это 1.88% от тика.

    Разница между этими числами и есть разница между «блок-энтити ничего не
    стоят» и «блок-энтити 1.9% — ниже порога опровержения, но не ноль».
    """
    p = pp.parse_zip(profile_zip)
    assert p.share_of_tick("blockEntities") == pytest.approx(0.29 / 15.45 * 100, abs=0.01)
    assert p.share_of_tick("blockEntities") == pytest.approx(1.88, abs=0.01)
    # Сырое число из колонки «% от общего» — НЕ ответ.
    assert p.share_of_tick("blockEntities") > 6 * 0.29


def test_shares_of_all_subsystems_sum_to_about_the_tick(pp, profile_zip):
    p = pp.parse_zip(profile_zip)
    total = sum(
        p.share_of_tick(s) for s in ("entities", "chunkSource", "blockEntities", "tickPending")
    )
    # levels=98.28% тика, остальное — connection и мелочь; 97% сходится.
    assert 95.0 < total < 100.0


def test_dominant_subsystems_are_entities_and_chunks(pp, profile_zip):
    """Главный вывод сцены 1 пинится числом, а не пересказом."""
    p = pp.parse_zip(profile_zip)
    assert p.share_of_tick("entities") == pytest.approx(54.11, abs=0.05)
    assert p.share_of_tick("chunkSource") == pytest.approx(39.87, abs=0.05)
    # 54.11 / 1.88 = 28.8: сущности дороже блок-энтити почти в 29 раз.
    assert p.share_of_tick("entities") / p.share_of_tick("blockEntities") == pytest.approx(
        28.8, abs=0.2
    )


def test_missing_section_returns_none_not_zero(pp, profile_zip):
    """Отсутствие секции и нулевая доля — РАЗНЫЕ вещи; молча вернуть 0 нельзя."""
    p = pp.parse_zip(profile_zip)
    assert p.share_of_tick("nosuchsection") is None


def test_counts_read_from_csv(pp, profile_zip):
    """Шапка CSV не должна попадать в счётчик."""
    p = pp.parse_zip(profile_zip)
    assert p.counts["overworld.block_entities"] == 1
    assert p.counts["overworld.chunks"] == 2
