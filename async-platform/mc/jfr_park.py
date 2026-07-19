"""ThreadPark по МЕСТУ в стеке: отличить RWLock-contention от idle-пула и барьера.

Задача mc-contention-locate. parkedClass=AbstractQueuedSynchronizer общий для
ReentrantReadWriteLock / FutureTask / ForkJoinPool — какой именно лок держит
воркеров, видно только по стеку. Группируем по первому осмысленному кадру
(не Unsafe.park / LockSupport.park) и по корзине длительности.

Корзины: idle между тиками длинный (десятки мс), барьер/лок-contention внутри
фазы короткий. На НАСЫЩЕННОЙ сцене (N=21904, тик>50мс) idle~0 и остаётся суть.

Использование:
    python jfr_park.py D:/mc-core-work/jfr/n21904_t16.jfr --threads MCW-Entity --ticks 380
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
SKIP = ("jdk.internal.misc.Unsafe.park", "java.util.concurrent.locks.LockSupport.park")


def dur_s(text: str) -> float:
    m = DUR.fullmatch(text)
    if not m:
        return 0.0
    h, mm, s = m.groups()
    return int(h or 0) * 3600 + int(mm or 0) * 60 + float(s)


def frame_name(fr: dict) -> str:
    meth = fr.get("method", {})
    typ = (meth.get("type") or {}).get("name", "?").replace("/", ".")
    return f"{typ}.{meth.get('name', '?')}"


def site(frames: list[dict]) -> str:
    """Первые 3 кадра, пропустив тривиальные park-обёртки — идентифицируют лок."""
    named = [frame_name(f) for f in frames]
    meaningful = [n for n in named if not any(n.startswith(s) for s in SKIP)]
    return " <- ".join(meaningful[:3]) if meaningful else (named[0] if named else "?")


def bucket(d: float) -> str:
    if d < 0.002:
        return "<2мс"
    if d < 0.010:
        return "2-10мс"
    return ">10мс(idle?)"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("jfr", type=Path)
    ap.add_argument("--threads", default=None, help="префикс потока (MCW-Entity / Server)")
    ap.add_argument("--ticks", type=int, default=None)
    ap.add_argument("--depth", type=int, default=12)
    args = ap.parse_args()
    if not args.jfr.exists():
        sys.exit(f"нет {args.jfr}")

    raw = subprocess.run(
        [
            str(JFR),
            "print",
            "--json",
            "--events",
            "jdk.ThreadPark",
            "--stack-depth",
            str(args.depth),
            str(args.jfr),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if raw.returncode != 0:
        sys.exit(f"jfr print: {raw.stderr[:200]}")
    events = json.loads(raw.stdout)["recording"]["events"]

    by_site: dict[str, list] = defaultdict(lambda: [0.0, 0])  # site -> [sum,count]
    by_bucket: dict[str, list] = defaultdict(lambda: [0.0, 0])
    site_bucket: dict[tuple, float] = defaultdict(float)
    total = 0.0
    kept = 0
    for e in events:
        v = e["values"]
        thread = (v.get("eventThread") or {}).get("javaName", "?")
        if args.threads and not thread.startswith(args.threads):
            continue
        d = dur_s(v["duration"])
        frames = (v.get("stackTrace") or {}).get("frames", [])
        s = site(frames)
        b = bucket(d)
        by_site[s][0] += d
        by_site[s][1] += 1
        by_bucket[b][0] += d
        by_bucket[b][1] += 1
        site_bucket[(s, b)] += d
        total += d
        kept += 1

    flt = f" (поток ~{args.threads}*)" if args.threads else ""
    print(f"Файл: {args.jfr}")
    print(f"ThreadPark учтено {kept}{flt}, сумма {total * 1e3:.1f} мс")
    if args.ticks:
        print(f"на тик (÷{args.ticks}): {total * 1e3 / args.ticks:.3f} мс/тик суммарно по потокам")

    print("\n  По КОРЗИНЕ длительности:")
    print(f"    {'корзина':<16}{'сумма,мс':>12}{'событий':>10}")
    for b in ("<2мс", "2-10мс", ">10мс(idle?)"):
        s, c = by_bucket.get(b, [0.0, 0])
        print(f"    {b:<16}{s * 1e3:>12.1f}{c:>10}")

    print("\n  По МЕСТУ в стеке (топ-15 по сумме), с разбивкой по корзине:")
    rows = sorted(by_site.items(), key=lambda kv: kv[1][0], reverse=True)[:15]
    for s, (ssum, sc) in rows:
        parts = []
        for b in ("<2мс", "2-10мс", ">10мс(idle?)"):
            bs = site_bucket.get((s, b), 0.0)
            if bs > 0:
                parts.append(f"{b}={bs * 1e3:.0f}")
        print(f"    [{ssum * 1e3:9.1f} мс | {sc:6} соб] {s}")
        print(f"        {'  '.join(parts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
