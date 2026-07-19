"""Запуск ядра 26.2 с патч-jar ПЕРЕД ванильным в classpath — в обход bundler.

Bundler (Main-Class: net.minecraft.bundler.Main) распаковывает и сверяет SHA-256
внутренних jar по META-INF/*.list — патчить versions/26.2/server-26.2.jar на
месте нельзя. Настоящий main (META-INF/main-class = net.minecraft.server.Main)
стартует обычным classpath; классы из патч-jar стоят РАНЬШЕ ванильных и потому
затеняют их. Сам jar Mojang не трогается и не перепаковывается.

Jar Mojang ПОДПИСАН (META-INF/MOJANGCS.SF|RSA) и не пускает неподписанные
классы в свои пакеты (SecurityException: signer information does not match) —
поэтому ОБЕ руки бегут на неподписанной копии из D:/mc-core-work/jars
(strip_sign: снять .SF/.RSA), и единственная разница рук — патч-jar впереди.

Патч-jar собирается в D:/mc-core-work — ВНЕ репозитория (dead end #34):
    javac (JDK 25) -cp server-26.2.jar;libraries/**  ->  out/mcw-patches.jar

Использование:
    python patched_core.py            # проба конвейера, обе руки
    python patched_core.py --arm patched --observe 15
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import tick_probe as tp

SERVER_DIR = tp.SERVER_DIR
INNER_JAR = tp.Path("D:/mc-core-work/jars/server-26.2-unsigned.jar")
LIBS_DIR = SERVER_DIR / "libraries"
PATCH_JAR = tp.Path("D:/mc-core-work/out/mcw-patches.jar")
MAIN_CLASS = "net.minecraft.server.Main"  # META-INF/main-class бандлера
MARKER = "[MCW-PATCH]"
# Строки, за которыми в логе прячутся тихие провалы (урок десяти случаев эпика).
ERROR_MARKS = ("ERROR", "Exception", "Incorrect argument", "Unknown", "Expected")


def build_command(java: tp.Path, patched: bool) -> list[str]:
    if not INNER_JAR.exists():
        sys.exit(f"нет {INNER_JAR} — bundler ещё не распаковывал сервер?")
    if patched and not PATCH_JAR.exists():
        sys.exit(f"нет {PATCH_JAR} — собери патч-jar в D:/mc-core-work")
    entries = [str(PATCH_JAR)] if patched else []
    entries.append(str(INNER_JAR))
    entries.extend(str(j) for j in sorted(LIBS_DIR.rglob("*.jar")))
    # MCW_JAVA_OPTS: -Dmcw.parallel/-Dmcw.threads/-Dmcw.minEntities и т.п.
    extra = os.environ.get("MCW_JAVA_OPTS", "").split()
    return [str(java), "-Xmx4G", *extra, "-cp", ";".join(entries), MAIN_CLASS, "nogui"]


def run_arm(arm: str, observe_s: float, java: tp.Path) -> bool:
    """Гард двусторонний (convention #22): рука patched ОБЯЗАНА показать маркер,
    рука vanilla ОБЯЗАНА его не иметь. Тик обязан идти в обеих."""
    patched = arm == "patched"
    print(f"\n{'=' * 62}\n  РУКА: {arm}\n{'=' * 62}")
    srv = tp.ServerProc(java, cmd=build_command(java, patched))
    try:
        if not srv.await_line(tp.RE_DONE, tp.STARTUP_TIMEOUT_S):
            print("  ПРОВАЛ: сервер не поднялся за отведённое время")
            return False
        g0 = srv.query_gametime()
        time.sleep(observe_s)
        g1 = srv.query_gametime()
    finally:
        srv.stop()

    ticks, expected = g1 - g0, observe_s * tp.TICKS_PER_SECOND
    ticking = ticks >= expected * 0.9
    marker = any(MARKER in ln for ln in srv.transcript)
    errors = [ln for ln in srv.transcript if any(m in ln for m in ERROR_MARKS)]

    print(
        f"  тик: {ticks} за {observe_s:.0f} с (ожидание >= {expected * 0.9:.0f}) — {'ИДЁТ' if ticking else 'НЕ ИДЁТ'}"
    )
    print(
        f"  маркер {MARKER}: {'ЕСТЬ' if marker else 'НЕТ'} (обязан {'быть' if patched else 'отсутствовать'})"
    )
    if errors:
        print(f"  подозрительных строк в логе: {len(errors)}")
        for ln in errors[:10]:
            print(f"    {ln}")
    ok = ticking and (marker == patched) and not errors
    print(f"  РУКА {arm}: {'OK' if ok else 'ПРОВАЛ'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--arm", choices=["patched", "vanilla", "both"], default="both")
    ap.add_argument("--observe", type=float, default=15.0, help="окно наблюдения тика, сек")
    args = ap.parse_args()

    java = tp.find_java()
    arms = ["patched", "vanilla"] if args.arm == "both" else [args.arm]
    results = {arm: run_arm(arm, args.observe, java) for arm in arms}

    ok = all(results.values())
    print(f"\nВЕРДИКТ: {'конвейер доказан' if ok else 'конвейер НЕ доказан'} ({results})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
