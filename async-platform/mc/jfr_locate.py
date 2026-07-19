"""Локализация contention замером JFR: окно записи вокруг замера MSPT.

Задача mc-contention-locate. Стенд ab_bench переиспользуется как есть (сцена,
гарды, чтение счётчиков), сверху — управляемое окно JFR через jcmd против pid
живого сервера. Пишем ТОЛЬКО установившийся тик: JFR.start ПОСЛЕ стабилизации,
JFR.stop до teardown — иначе в blocked-time попадёт постройка сцены (21904
summon = структурная мутация трекинга, это НЕ цена тика).

События (locate.jfc): jdk.JavaMonitorEnter (порог 0 -> все контендящие входы) и
jdk.ThreadPark (барьер f.get / RWLock / idle пула). Разбор — jfr view / jfr_sum.

Использование:
    python jfr_locate.py --side 80  --threads 16          # N=6400,  JFR вкл
    python jfr_locate.py --side 148 --threads 16          # N=21904
    python jfr_locate.py --side 80  --threads 1           # E: 1 поток, контроль
    python jfr_locate.py --side 80  --threads 16 --no-jfr # для чередования AC#5
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import ab_bench as ab
import patched_core as pc
import tick_probe as tp

JDK = tp.find_java().parent
JCMD = JDK / "jcmd.exe"
JFR_DIR = Path("D:/mc-core-work/jfr")
JFC = JFR_DIR / "locate.jfc"


def jcmd(pid: int, *args: str) -> str:
    """Вызов jcmd против pid сервера. Возвращает stdout; кидает при ненулевом коде."""
    out = subprocess.run(
        [str(JCMD), str(pid), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if out.returncode != 0:
        raise RuntimeError(f"jcmd {args} -> rc={out.returncode}: {out.stderr.strip()}")
    return out.stdout


def tail_lines(srv: tp.ServerProc) -> list[str]:
    return [ln.split("]")[-1].strip() for ln in srv.transcript if "[MCW-TAIL]" in ln]


def run(
    side: int,
    threads: int,
    use_jfr: bool,
    samples: int,
    gap: float,
    stabilize: float,
    window: float,
    tag: str,
) -> dict:
    parallel = threads > 1
    want = ab.mob_count(side)
    opts = [f"-Dmcw.threads={threads}", "-Dmcw.tailProfile=true", "-Dmcw.tailEvery=20"]
    opts.append("-Dmcw.parallel=true" if parallel else "-Dmcw.parallel=false")
    import os

    os.environ["MCW_JAVA_OPTS"] = " ".join(opts)
    print(
        f"\n{'=' * 66}\n  {tag}: side={side} N={want} threads={threads} "
        f"JFR={'ВКЛ' if use_jfr else 'выкл'}\n{'=' * 66}",
        flush=True,
    )
    print(f"  MCW_JAVA_OPTS={os.environ['MCW_JAVA_OPTS']}", flush=True)

    java = tp.find_java()
    srv = tp.ServerProc(java, cmd=pc.build_command(java, patched=True))
    jfr_path = JFR_DIR / f"{tag}.jfr"
    result: dict = {
        "tag": tag,
        "side": side,
        "n": want,
        "threads": threads,
        "jfr": use_jfr,
        "ok": False,
    }
    try:
        if not srv.await_line(tp.RE_DONE, tp.STARTUP_TIMEOUT_S):
            raise RuntimeError("сервер не поднялся")
        pid = srv.proc.pid

        ab.send_batch(srv, ab.scene_setup(side))
        ab.verify_gamerules(srv)
        ab.verify_no_cmd_errors(srv, "постройка сцены")
        ab.send_batch(srv, ab.scene_mobs(side, 1, ab.MOB[0]))
        time.sleep(stabilize)

        n_before = ab.read_count(srv, side)
        if n_before != want:
            n_global = ab.read_count_sel(srv, f"@e[type={ab.MOB[0]}]")
            raise RuntimeError(
                f"сцена не собралась: в зоне {n_before}, глобально {n_global}, ожидалось {want}"
            )
        print(f"  зомби до замера: {n_before}", flush=True)

        if use_jfr:
            print(jcmd(pid, "JFR.start", "name=locate", f"settings={JFC}").strip(), flush=True)
        g_before = srv.query_gametime()
        t_before = time.monotonic()
        mspt = ab.measure_mspt(srv, samples, gap)
        # окно JFR закрываем ровно на установившемся тике, до teardown
        extra = window - (time.monotonic() - t_before)
        if extra > 0:
            time.sleep(extra)
        ticks = srv.query_gametime() - g_before
        wall = time.monotonic() - t_before
        if use_jfr:
            print(jcmd(pid, "JFR.stop", "name=locate", f"filename={jfr_path}").strip(), flush=True)
        n_after = ab.read_count(srv, side)

        tails = tail_lines(srv)
        marker = any(pc.MARKER in ln for ln in srv.transcript)
        errors = [ln for ln in srv.transcript if ab.is_real_error(ln)]
        tps = ticks / wall if wall else 0.0
    finally:
        try:
            for c in ab.purge_cmds():
                srv.send(c)
            srv.send("save-all flush")
            time.sleep(4)
        except Exception:
            pass
        srv.stop()

    import statistics

    drift = abs(n_after - n_before) / n_before if n_before else 1.0
    ok = (
        marker
        and not errors
        and drift <= 0.02
        and tps >= 1.0
        and (not use_jfr or jfr_path.exists())
    )
    result.update(
        {
            "mspt_median": statistics.median(mspt) if mspt else 0.0,
            "mspt": mspt,
            "n_before": n_before,
            "n_after": n_after,
            "drift": drift,
            "tps": tps,
            "tails": tails,
            "errors": errors[:5],
            "jfr_path": str(jfr_path) if use_jfr else None,
            "ok": ok,
        }
    )
    print(
        f"  MSPT: {[f'{m:.1f}' for m in mspt]} медиана "
        f"{result['mspt_median']:.2f} | TPS {tps:.1f} | дрейф {drift:.1%}",
        flush=True,
    )
    print(f"  [MCW-TAIL] последних {min(3, len(tails))}:", flush=True)
    for ln in tails[-3:]:
        print(f"    {ln}", flush=True)
    print(
        f"  маркер={'ЕСТЬ' if marker else 'НЕТ'} ошибок={len(errors)} "
        f"jfr={'записан' if (use_jfr and jfr_path.exists()) else ('—' if not use_jfr else 'НЕТ')}"
        f" -> {'OK' if ok else 'ПРОВАЛ'}",
        flush=True,
    )
    for ln in errors[:5]:
        print(f"    {ln}", flush=True)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--side", type=int, default=80)
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--mob", default="zombie")
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--gap", type=float, default=6.0)
    ap.add_argument("--stabilize", type=float, default=20.0)
    ap.add_argument("--window", type=float, default=20.0, help="мин. длина окна JFR, с")
    ap.add_argument("--no-jfr", action="store_true")
    ap.add_argument("--tag", default=None)
    args = ap.parse_args()

    if not JFC.exists():
        sys.exit(f"нет {JFC}")
    ab.MOB[0] = args.mob
    tag = args.tag or f"n{args.side * args.side}_t{args.threads}{'_nojfr' if args.no_jfr else ''}"

    pause_orig = tp.read_pause_property()
    ab._set_pause(0)
    print(f"pause-when-empty-seconds: {pause_orig} -> 0")
    try:
        r = run(
            args.side,
            args.threads,
            not args.no_jfr,
            args.samples,
            args.gap,
            args.stabilize,
            args.window,
            tag,
        )
    finally:
        ab._set_pause(pause_orig)
        print(f"pause-when-empty-seconds восстановлен в {pause_orig}")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
