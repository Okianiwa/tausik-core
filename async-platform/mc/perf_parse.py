"""Разобрать отчёты /perf и свести доли подсистем ОТ ТИКА, а не от wall-времени.

ЗАЧЕМ ОТДЕЛЬНЫЙ СКРИПТ, А НЕ ЧТЕНИЕ ГЛАЗАМИ. В дереве /perf первая строка —
`nextTickWait`, и это СОН сервера между тиками, а не работа. На незагруженном
сервере он занимает 84-99% wall-времени. Колонка «% от общего» считает от wall
ВМЕСТЕ СО СНОМ, поэтому в ней любая подсистема на любой сцене читается как
«0.29%» — и вывод «блок-энтити ничего не стоят» получается АВТОМАТИЧЕСКИ,
независимо от реальности (memory #42).

Правильная величина — доля от РАБОТЫ: share = subsystem_of_total / tick_of_total.
Она здесь считается в коде, чтобы её нельзя было забыть.

Использование:
    python perf_parse.py                       # все архивы в server/debug/profiling
    python perf_parse.py --last 3              # последние три
    python perf_parse.py --zip <path> ...      # конкретные
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROFILING_DIR = HERE / "server" / "debug" / "profiling"

RE_HEADER_TICKS = re.compile(r"Tick span: (\d+) ticks")
RE_HEADER_MS = re.compile(r"Time span: (\d+) ms")
RE_TPS = re.compile(r"approximately ([\d.]+) ticks per second")
# [04] |   |   |   |   blockEntities(202/1) - 55.55%/8.36%
RE_NODE = re.compile(r"^\[(\d\d)\]\s+([|\s]*)([^(]+)\((\d+)/(\d+)\)\s+-\s+([\d.]+)%/([\d.]+)%")

# Подсистемы, о которых спрашивает AC #4. Ключ — имя секции в дереве /perf,
# значение — человеческое имя. Ищутся ТОЛЬКО под уровнем overworld/tick, иначе
# одноимённые секции из nether/end подмешаются в число.
SUBSYSTEMS = {
    "entities": "сущности (мобы, предметы, игрок)",
    "chunkSource": "чанки (загрузка/генерация/спавн)",
    "blockEntities": "блок-энтити",
    "tickPending": "блок- и жидкостные тики (редстоун, вода)",
    "blockEvents": "блок-события",
    "raid": "рейды",
    "weather": "погода",
    "entityManagement": "управление сущностями",
    "scheduledFunctions": "функции по расписанию",
    "world border": "граница мира",
}


@dataclass
class Node:
    depth: int
    name: str
    pct_of_parent: float
    pct_of_total: float


@dataclass
class Profile:
    path: Path
    ticks: int
    span_ms: int
    tps: float
    tick_of_total: float
    nodes: list[Node]
    counts: dict[str, int] = field(default_factory=dict)

    def share_of_tick(self, section: str) -> float | None:
        """Доля секции ОТ РАБОТЫ тика, в процентах. None — секции нет.

        Берётся первое вхождение под overworld: дерево содержит одноимённые
        секции в nether/end, и их числа к нашей сцене не относятся.
        """
        for n in self.nodes:
            if n.name == section and n.depth == 4:
                return n.pct_of_total / self.tick_of_total * 100 if self.tick_of_total else 0.0
        return None


def parse_zip(path: Path) -> Profile:
    with zipfile.ZipFile(path) as z:
        txt = z.read("server/profiling.txt").decode("utf-8", "replace")
        counts = {}
        for dim in ("overworld", "the_nether", "the_end"):
            for what in ("block_entities", "entities", "chunks"):
                name = f"server/levels/minecraft/{dim}/{what}.csv"
                try:
                    body = z.read(name).decode("utf-8", "replace").strip().splitlines()
                    counts[f"{dim}.{what}"] = max(len(body) - 1, 0)  # минус шапка
                except KeyError:
                    counts[f"{dim}.{what}"] = 0

    ticks = int(m.group(1)) if (m := RE_HEADER_TICKS.search(txt)) else 0
    span = int(m.group(1)) if (m := RE_HEADER_MS.search(txt)) else 0
    tps = float(m.group(1)) if (m := RE_TPS.search(txt)) else 0.0

    nodes: list[Node] = []
    tick_of_total = 0.0
    seen_dump = False
    for line in txt.splitlines():
        if "--- BEGIN PROFILE DUMP ---" in line:
            seen_dump = True
            continue
        if "COUNTER DUMP" in line:
            break
        if not seen_dump:
            continue
        m = RE_NODE.match(line)
        if not m:
            continue
        depth = int(m.group(1))
        name = m.group(3).strip()
        node = Node(depth, name, float(m.group(6)), float(m.group(7)))
        nodes.append(node)
        # `tick` на уровне [00] — вся работа сервера за вычетом сна.
        if depth == 0 and name == "tick":
            tick_of_total = node.pct_of_total
    return Profile(path, ticks, span, tps, tick_of_total, nodes, counts)


def report(profiles: list[Profile]) -> None:
    print(f"{'=' * 78}\n  ПРОФИЛИ: {len(profiles)}\n{'=' * 78}")
    for p in profiles:
        print(
            f"  {p.path.name}: {p.ticks} тиков за {p.span_ms} мс ({p.tps} TPS), "
            f"работа = {p.tick_of_total:.2f}% wall, сон = {100 - p.tick_of_total:.2f}%"
        )

    live = [p for p in profiles if p.ticks > 0]
    if not live:
        sys.exit("ни один профиль не застал ни одного тика — числа брать нельзя (AC #2)")

    print(
        f"\n  Загруженность: тик занимает {statistics.mean(p.tick_of_total for p in live):.1f}% "
        f"wall-времени; остальное сервер СПИТ."
    )
    print(
        f"  Мир: блок-энтити={live[-1].counts.get('overworld.block_entities')}, "
        f"сущности={live[-1].counts.get('overworld.entities')}, "
        f"чанки={live[-1].counts.get('overworld.chunks')} (overworld)"
    )

    print(f"\n{'подсистема':<42}{'% от ТИКА (среднее)':>21}{'разброд':>14}")
    print("-" * 78)
    rows = []
    for section, human in SUBSYSTEMS.items():
        vals = [s for p in live if (s := p.share_of_tick(section)) is not None]
        if not vals:
            continue
        mean = statistics.mean(vals)
        spread = f"{min(vals):.2f}–{max(vals):.2f}" if len(vals) > 1 else "—"
        rows.append((mean, human, spread))
    for mean, human, spread in sorted(rows, reverse=True):
        print(f"{human:<42}{mean:>20.2f}%{spread:>14}")

    total = sum(r[0] for r in rows)
    print("-" * 78)
    print(f"{'сумма перечисленного':<42}{total:>20.2f}%")
    print(f"{'(остальное — unspecified и мелочь)':<42}{100 - total:>20.2f}%")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--zip", type=Path, nargs="*", help="конкретные архивы")
    ap.add_argument(
        "--last", type=int, default=0, help="взять N последних из server/debug/profiling"
    )
    args = ap.parse_args()

    if args.zip:
        paths = list(args.zip)
    else:
        paths = sorted(PROFILING_DIR.glob("*.zip"))
        if args.last:
            paths = paths[-args.last :]
    if not paths:
        sys.exit(f"архивов не найдено в {PROFILING_DIR}")

    report([parse_zip(p) for p in paths])
    return 0


if __name__ == "__main__":
    sys.exit(main())
