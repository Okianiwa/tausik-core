"""Точная сумма blocked-time из .jfr: по классу монитора/парковки и по потоку.

Задача mc-contention-locate, AC #1 (СУММА времени блокировок с числом) и анализ
дисбаланса (по потокам MCW-Entity-*). Встроенные `jfr view contention-by-*`
дают Count/Avg/P90/Max, но не точную сумму — её считаем здесь из --json.

stack-depth 1: для суммы по классу стек не нужен, зато JSON компактен даже при
сотнях тысяч событий (место в стеке — отдельно, `jfr view contention-by-site`).

Использование:
    python jfr_sum.py D:/mc-core-work/jfr/n6400_t16.jfr
    python jfr_sum.py <file.jfr> --threads MCW-Entity   # только воркеры
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import tick_probe as tp

JDK = tp.find_java().parent
JFR = JDK / "jfr.exe"
DUR = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?([\d.]+)S")


def dur_s(text: str) -> float:
    m = DUR.fullmatch(text)
    if not m:
        return 0.0
    h, mm, s = m.groups()
    return (int(h or 0) * 3600) + (int(mm or 0) * 60) + float(s)


def summary_counts(path: Path) -> dict[str, int]:
    out = subprocess.run(
        [str(JFR), "summary", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    counts: dict[str, int] = {}
    for ev in ("jdk.JavaMonitorEnter", "jdk.ThreadPark"):
        m = re.search(rf"{re.escape(ev)}\s+(\d+)", out)
        counts[ev] = int(m.group(1)) if m else 0
    return counts


def load_events(path: Path, event: str) -> list[dict]:
    """jfr print --json одного типа события, stack-depth 1."""
    raw = subprocess.run(
        [str(JFR), "print", "--json", "--events", event, "--stack-depth", "1", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if raw.returncode != 0:
        raise RuntimeError(f"jfr print {event}: rc={raw.returncode}: {raw.stderr[:200]}")
    return json.loads(raw.stdout)["recording"]["events"]


def aggregate(events: list[dict], class_field: str, thr_filter: str | None):
    """Возвращает (by_class, by_thread, total_s, n, n_kept). Фильтр по префиксу потока."""
    by_class: dict[str, list] = defaultdict(lambda: [0.0, 0])  # class -> [sum_s, count]
    by_thread: dict[str, list] = defaultdict(lambda: [0.0, 0])
    total_s = 0.0
    n = len(events)
    n_kept = 0
    for e in events:
        v = e["values"]
        thread = (v.get("eventThread") or {}).get("javaName", "?")
        if thr_filter and not thread.startswith(thr_filter):
            continue
        n_kept += 1
        d = dur_s(v["duration"])
        cls = v.get(class_field)
        cls_name = (cls or {}).get("name", "<none>") if isinstance(cls, dict) else str(cls)
        cls_name = cls_name.replace("/", ".")
        by_class[cls_name][0] += d
        by_class[cls_name][1] += 1
        by_thread[thread][0] += d
        by_thread[thread][1] += 1
        total_s += d
    return by_class, by_thread, total_s, n, n_kept


def show(title: str, agg: dict[str, list], limit: int = 15) -> None:
    print(f"\n  {title}")
    print(f"    {'класс / поток':<52}{'сумма, мс':>12}{'событий':>10}{'ср, мкс':>10}")
    rows = sorted(agg.items(), key=lambda kv: kv[1][0], reverse=True)[:limit]
    for name, (s, c) in rows:
        avg_us = (s / c * 1e6) if c else 0.0
        print(f"    {name[:52]:<52}{s * 1e3:>12.2f}{c:>10}{avg_us:>10.1f}")


def analyze_event(
    path: Path, event: str, class_field: str, thr_filter: str | None, ticks: int | None
) -> None:
    events = load_events(path, event)
    by_class, by_thread, total_s, n, n_kept = aggregate(events, class_field, thr_filter)
    print(
        f"\n{'=' * 74}\n  {event}: событий всего {n}, "
        f"учтено {n_kept}{f' (поток ~{thr_filter}*)' if thr_filter else ''}\n{'=' * 74}"
    )
    print(f"  СУММАРНОЕ время блокировок: {total_s * 1e3:.2f} мс за окно")
    if ticks:
        print(
            f"  на тик (÷{ticks}): {total_s * 1e3 / ticks:.3f} мс/тик | "
            f"÷16 потоков: {total_s * 1e3 / ticks / 16:.3f} мс/тик/поток"
        )
    show(f"по {class_field}", by_class)
    show("по потоку (дисбаланс)", by_thread, limit=20)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("jfr", type=Path)
    ap.add_argument("--threads", default=None, help="префикс имени потока (напр. MCW-Entity)")
    ap.add_argument("--ticks", type=int, default=None, help="тиков в окне — для нормировки на тик")
    args = ap.parse_args()
    if not args.jfr.exists():
        sys.exit(f"нет {args.jfr}")

    counts = summary_counts(args.jfr)
    print(f"Файл: {args.jfr}")
    print(
        f"События: JavaMonitorEnter={counts['jdk.JavaMonitorEnter']}, "
        f"ThreadPark={counts['jdk.ThreadPark']}"
    )
    if sum(counts.values()) > 2_000_000:
        print("  ВНИМАНИЕ: >2М событий — JSON-разбор тяжёлый, возможно поднять порог в locate.jfc")

    analyze_event(args.jfr, "jdk.JavaMonitorEnter", "monitorClass", args.threads, args.ticks)
    analyze_event(args.jfr, "jdk.ThreadPark", "parkedClass", args.threads, args.ticks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
