"""A/B стенд: одна сцена — два ядра (ваниль / переписанное), MSPT в АБСОЛЮТЕ.

Сцена: плотная платформа зомби в загоне из barrier. Цель — ЛАГ (AC #5): на
выживаче с тиком 4 мс экономить нечего (memory #49), поэтому мобов столько,
чтобы ваниль НЕ справлялась (MSPT >= 50 = TPS < 20).

Защиты, купленные ошибками этого эпика:
  * gamerules ЧИТАЮТСЯ ОБРАТНО (memory #56: имена сменились в 26.2, сервер
    отвечал `Incorrect argument`, а прогон шёл дальше и врал);
  * счётчик зомби В ЗОНЕ снимается ДО и ПОСЛЕ замера — горизонт годности
    закрыт гардом, который ПАДАЕТ (memory #27), а не комментарием;
  * руки ЧЕРЕДУЮТСЯ (V,P,V,P,...) — иначе дрейф машины припишется ядру
    (dead end: «дрейф +17%» из одного замера);
  * лог грепается на Exception/ошибки — тихий провал не должен выглядеть
    как результат.

Использование:
    python ab_bench.py --runs 3            # 3 прогона на руку, чередуя
    python ab_bench.py --runs 1 --side 40  # быстрая проба
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
import time

import patched_core as pc
import tick_probe as tp

X0, Z0 = 3000, 3000
Y_FLOOR, Y_MOB = 99, 100
WALL_H = 4
OBJ = "mcwcnt"
CRASH_DIR = tp.SERVER_DIR / "crash-reports"

RE_COUNT = re.compile(r"#zc has (\d+) \[" + OBJ + r"\]")
RE_GAMERULE = re.compile(r"Gamerule (\S+) is currently set to: (\S+)")
RE_KEEPUP = re.compile(r"Can't keep up|Running \d+ms or \d+ ticks behind")

GAMERULES = {
    "spawn_mobs": "false",
    "advance_time": "false",
    "advance_weather": "false",
    "random_tick_speed": "0",
    # Плотная толпа ДАВИТ САМА СЕБЯ: при cramming=24 зомби в куче умирают, и это
    # не только уносит сцену (20 из 6400 пропали за прогон), но и рвёт ядро —
    # смерть моба = структурная мутация трекинга из воркера. Сцена обязана быть
    # СТАЦИОНАРНОЙ: мерим цену тика, а не цену похорон.
    "max_entity_cramming": "0",
    # Дроп с мёртвого моба — сам по себе источник ItemEntity, а те деспавнятся
    # и структурно мутируют трекинг. Убитые в teardown зомби иначе оставляют
    # плоть, которая тикает на старте СЛЕДУЮЩЕЙ руки.
    "entity_drops": "false",
}


def purge_cmds() -> list[str]:
    """Чистка сцены ОТРИЦАНИЕМ, а не списком типов.

    Список типов — источник самого дорогого брака эпика. Прежняя уборка знала
    про zombie/item/experience_orb, а 339 zombie_villager + 296 slime + 109
    drowned от смешанного теста прошлой сессии пережили и teardown, и setup:
    ядро тикало 7170 сущностей при сцене 6400, ваниль дала 126.3 мс вместо
    90.5 — 28% результата, выглядевших как факт (дрейф 0.0%, ошибок 0).
    Перечисление не может покрыть то, чего в нём нет; отрицание может.

    Блоки (barrier загона, пол, чужие фермы) сущностями не являются — kill их
    не трогает, поэтому сцене это ничем не грозит.
    """
    return ["kill @e[type=!player]"]


def scene_setup(side: int) -> list[str]:
    """Загон + пол + чистка. barrier по периметру держит плотность: без стен
    зомби расходятся за время замера и MSPT уплывает вместе с ними."""
    x1, z1 = X0 + side - 1, Z0 + side - 1
    return [
        f"forceload add {X0 - 1} {Z0 - 1} {x1 + 1} {z1 + 1}",
        "difficulty normal",
        *[f"gamerule {k} {v}" for k, v in GAMERULES.items()],
        "time set midnight",
        *purge_cmds(),
        f"fill {X0 - 1} {Y_FLOOR} {Z0 - 1} {x1 + 1} {Y_FLOOR} {z1 + 1} stone",
        # ПОСЛОЙНО, а не одним объёмом: у /fill предел 32768 блоков, и куб
        # 82x82x5 = 33620 его перебирает. Сервер отвечает «Too many blocks» и
        # НИЧЕГО не делает — зомби спавнятся в грунт и задыхаются, сцена тает.
        # Одиннадцатый случай «громкий отказ был, но не прочитан» за эпик.
        *[
            f"fill {X0 - 1} {y} {Z0 - 1} {x1 + 1} {y} {z1 + 1} air"
            for y in range(Y_MOB, Y_MOB + WALL_H + 1)
        ],
        f"fill {X0 - 1} {Y_MOB} {Z0 - 1} {X0 - 1} {Y_MOB + WALL_H} {z1 + 1} barrier",
        f"fill {x1 + 1} {Y_MOB} {Z0 - 1} {x1 + 1} {Y_MOB + WALL_H} {z1 + 1} barrier",
        f"fill {X0 - 1} {Y_MOB} {Z0 - 1} {x1 + 1} {Y_MOB + WALL_H} {Z0 - 1} barrier",
        f"fill {X0 - 1} {Y_MOB} {z1 + 1} {x1 + 1} {Y_MOB + WALL_H} {z1 + 1} barrier",
        f"scoreboard objectives add {OBJ} dummy",
    ]


def scene_mobs(side: int, step: int = 1, mob: str = "zombie") -> list[str]:
    return [
        f"summon {mob} {X0 + i} {Y_MOB} {Z0 + j} {{PersistenceRequired:1b}}"
        for i in range(0, side, step)
        for j in range(0, side, step)
    ]


def mob_count(side: int, step: int = 1) -> int:
    n = len(range(0, side, step))
    return n * n


MOB = ["zombie"]  # тип моба сцены; правится из main


def zone_selector(side: int) -> str:
    return f"@e[type={MOB[0]},x={X0 - 1},y={Y_MOB - 1},z={Z0 - 1},dx={side + 2},dy={WALL_H + 2},dz={side + 2}]"


def send_batch(srv: tp.ServerProc, cmds: list[str], chunk: int = 500, pause: float = 1.5) -> None:
    for i in range(0, len(cmds), chunk):
        for c in cmds[i : i + chunk]:
            srv.send(c)
        time.sleep(pause)


def read_count_sel(srv: tp.ServerProc, sel: str) -> int:
    srv.send(f"execute store result score #zc {OBJ} if entity {sel}")
    srv.send(f"scoreboard players get #zc {OBJ}")
    m = srv.await_line(RE_COUNT, 30.0)
    if not m:
        raise RuntimeError(f"сервер не ответил счётчиком для {sel}")
    return int(m.group(1))


def read_count(srv: tp.ServerProc, side: int) -> int:
    return read_count_sel(srv, zone_selector(side))


# Отказы, которые сервер печатает ВМЕСТО того, чтобы упасть. Каждый прятался
# за «правдоподобным числом»: «Too many blocks» стоил сцены, съеденной грунтом.
# «No entity was found» СЮДА НЕ ВХОДИТ: это штатный ответ kill по пустой сцене,
# а не отказ. Гард, кричащий на норму, быстро научит себя игнорировать.
RE_CMD_FAIL = re.compile(
    r"Too many blocks|Incorrect argument|Unknown or incomplete command|"
    r"Unable to summon|That position is not loaded"
)


# Шум ОКРУЖЕНИЯ, а не отказ ядра. Сервер ходит в api.minecraftservices.com за
# публичными ключами подписи чата; когда интернет тупит, в лог падает ERROR +
# Exception + Caused by — ровно три строки, и весь прогон уходит в брак. Поток
# `Yggdrasil Key Fetcher`, не `Server thread`; случается на старте, до сцены;
# на тик не влияет — доказано: точки, забракованные этим таймаутом (48.4/54.0/
# 59.6 мс), легли на модель, построенную по прогонам с ошибок=0, с ошибкой <3%.
# Тот же принцип, что у RE_CMD_FAIL с «No entity was found»: гард, кричащий на
# норму, быстро научит себя игнорировать. Список НАМЕРЕННО узкий — глушим
# конкретный внешний таймаут, а не «сетевые ошибки вообще».
ERROR_IGNORE = (
    "Yggdrasil",
    "api.minecraftservices.com",
    "authlib.exceptions.MinecraftClientException",
    "java.net.SocketTimeoutException",
)


def is_real_error(line: str) -> bool:
    """Отказ, за который прогон обязан быть забракован (в отличие от шума среды)."""
    if not ("Exception" in line or "/ERROR]" in line):
        return False
    return not any(mark in line for mark in ERROR_IGNORE)


def verify_no_cmd_errors(srv: tp.ServerProc, stage: str) -> None:
    bad = [ln for ln in srv.transcript if RE_CMD_FAIL.search(ln)]
    if bad:
        raise RuntimeError(f"сервер отверг команды на этапе {stage} ({len(bad)}): {bad[0]}")


def verify_gamerules(srv: tp.ServerProc) -> None:
    """Читаем ОБРАТНО каждое правило. Ловушка десятого случая: сервер отвечает
    `Incorrect argument` на переименованное правило, прогон идёт дальше."""
    for key, want in GAMERULES.items():
        srv.send(f"gamerule {key}")
        m = srv.await_line(RE_GAMERULE, 15.0)
        if not m:
            raise RuntimeError(f"нет ответа на `gamerule {key}` — правило существует?")
        got_key, got_val = m.group(1), m.group(2)
        if got_key != key or got_val != want:
            raise RuntimeError(
                f"gamerule {key}: ожидалось {want}, сервер отдал {got_key}={got_val}"
            )


def measure_mspt(srv: tp.ServerProc, samples: int, gap_s: float) -> list[float]:
    out = []
    for _ in range(samples):
        time.sleep(gap_s)
        _, avg = srv.query_tick(timeout=30.0)
        if avg <= 0:
            raise RuntimeError("tick query не отдал среднее время тика")
        out.append(avg)
    return out


def _set_pause(value: int) -> None:
    """ЛОВУШКА memory #35, вскрытая гардом TPS: сервер без игроков засыпает через
    60 с (pause-when-empty-seconds). И бьёт она ИЗБИРАТЕЛЬНО — по быстрой руке:
    ваниль не справляется (10 TPS) и потому всегда занята, а патч успевает,
    сервер простаивает и УХОДИТ В ПАУЗУ. То есть дефект стенда занижал бы ровно
    ту руку, которую мы проверяем. Гасим паузу на время замеров.
    """
    text = tp.PROPS.read_text(encoding="utf-8")
    new, n = re.subn(rf"^{tp.PAUSE_KEY}=.*$", f"{tp.PAUSE_KEY}={value}", text, flags=re.M)
    if n != 1:
        raise RuntimeError(f"ожидалось одно вхождение {tp.PAUSE_KEY}, найдено {n}")
    tp.PROPS.write_text(new, encoding="utf-8")


def run_once(
    arm: str,
    side: int,
    java: tp.Path,
    samples: int,
    gap_s: float,
    stabilize_s: float,
    step: int = 1,
) -> dict:
    patched = arm == "patched"
    want = mob_count(side, step)
    print(f"\n--- прогон: {arm} (side={side}, шаг={step}, зомби={want}) ---", flush=True)
    srv = tp.ServerProc(java, cmd=pc.build_command(java, patched))
    try:
        if not srv.await_line(tp.RE_DONE, tp.STARTUP_TIMEOUT_S):
            raise RuntimeError("сервер не поднялся")

        send_batch(srv, scene_setup(side))
        verify_gamerules(srv)
        verify_no_cmd_errors(srv, "постройка сцены")
        send_batch(srv, scene_mobs(side, step, MOB[0]))
        time.sleep(stabilize_s)

        n_before = read_count(srv, side)
        if n_before != want:
            # Недобор — это НЕ мелочь: он значит либо «команда не выполнилась»,
            # либо «сущность умерла». Второе для параллельной фазы смертельно
            # (структурная мутация из воркера), поэтому различаем ЧИСЛАМИ.
            summoned = sum(1 for ln in srv.transcript if "Summoned new" in ln)
            cmd_err = [
                ln
                for ln in srv.transcript
                if "Incorrect argument" in ln or "Unable to summon" in ln or "Expected" in ln
            ]
            # Различаем «умерли» и «ушли из зоны»: глобальный счёт по типу без
            # координат. Равен ожидаемому -> живы, но выдавлены за стены.
            n_global = read_count_sel(srv, f"@e[type={MOB[0]}]")
            raise RuntimeError(
                f"сцена не собралась: в зоне {n_before}, ГЛОБАЛЬНО {n_global}, ожидалось {want}; "
                f"рапортов Summoned={summoned}, ошибок команд={len(cmd_err)}"
                + (f"; первая: {cmd_err[0]}" if cmd_err else "")
            )
        print(f"  зомби до замера: {n_before}", flush=True)

        g_before = srv.query_gametime()
        t_before = time.monotonic()
        mspt = measure_mspt(srv, samples, gap_s)
        ticks = srv.query_gametime() - g_before
        wall = time.monotonic() - t_before
        tps = ticks / wall if wall else 0
        paused = any("Server empty" in ln for ln in srv.transcript)
        print(
            f"  тиков за замер: {ticks} за {wall:.0f} с = {tps:.1f} TPS | пауза в логе: {'ДА' if paused else 'нет'}",
            flush=True,
        )
        n_after = read_count(srv, side)

        stat = [ln for ln in srv.transcript if "MCW-STAT" in ln]
        marker = any(pc.MARKER in ln for ln in srv.transcript)
        errors = [ln for ln in srv.transcript if is_real_error(ln)]
    finally:
        # Teardown: мир СОХРАНЯЕТСЯ между прогонами, и зомби прошлой руки тикали
        # бы ещё до setup следующей — на старте, когда правила сцены (cramming)
        # ещё не применены. Каждый прогон обязан начинаться с пустого мира.
        try:
            for c in purge_cmds():
                srv.send(c)
            srv.send("save-all flush")
            time.sleep(4)
        except Exception:
            pass  # процесс уже мёртв — крах и так будет виден в вердикте
        srv.stop()

    # Гард горизонта: сцена ОБЯЗАНА пережить собственный замер. Дрейф >2%
    # означает, что мерили уже не то, что построили.
    drift = abs(n_after - n_before) / n_before
    lost = any("ПОТЕРЯ" in x for x in stat)
    # Замер на СПЯЩЕМ сервере даёт правдоподобный MSPT при стоящем мире.
    ticking = tps >= 1.0
    ok = marker == patched and not errors and drift <= 0.02 and not lost and ticking
    print(
        f"  MSPT: {[f'{m:.1f}' for m in mspt]} | зомби после: {n_after} (дрейф {drift:.1%})",
        flush=True,
    )
    print(
        f"  маркер={'ЕСТЬ' if marker else 'нет'} ошибок={len(errors)} -> {'OK' if ok else 'ПРОВАЛ'}",
        flush=True,
    )
    # Доказательство, что ядро РАБОТАЛО, а не просто быстро вернулось.
    for ln in [x for x in stat if "ПОТЕРЯ" in x][:2] or stat[-1:]:
        print(f"    {ln.split(']')[-1].strip()}", flush=True)
    for ln in errors[:5]:
        print(f"    {ln}", flush=True)
    return {
        "arm": arm,
        "mspt": mspt,
        "median": statistics.median(mspt),
        "n_before": n_before,
        "n_after": n_after,
        "ok": ok,
        "errors": errors[:5],
        "lost_work": any("ПОТЕРЯ" in x for x in stat),
        "tps": tps,
    }


def report(results: list[dict], side: int) -> bool:
    print(f"\n{'=' * 66}\n  A/B ИТОГ — сцена {side}x{side} = {side * side} зомби\n{'=' * 66}")
    by_arm: dict[str, list[float]] = {}
    for r in results:
        by_arm.setdefault(r["arm"], []).extend(r["mspt"])

    for arm, vals in by_arm.items():
        if not vals:
            print(f"  {arm:<9} НЕТ ДАННЫХ — все прогоны этой руки упали")
            continue
        med = statistics.median(vals)
        print(
            f"  {arm:<9} медиана {med:7.2f} мс | разброс {min(vals):.1f}-{max(vals):.1f} | n={len(vals)}"
        )

    bad = [r for r in results if not r["ok"]]
    if bad:
        print(f"\n  ПРОВАЛЕННЫХ ПРОГОНОВ: {len(bad)} — числа ниже брать НЕЛЬЗЯ")
        return False

    if by_arm.get("vanilla") and by_arm.get("patched"):
        v, p = statistics.median(by_arm["vanilla"]), statistics.median(by_arm["patched"])
        print(f"\n  ваниль {v:.2f} мс -> патч {p:.2f} мс | отношение {p / v:.2f}x")
        # Порог сцены (AC #5): на нелагающей сцене экономить нечего, и любой
        # вывод о выигрыше был бы выводом ни о чём.
        if v < 50:
            print(f"  ВНИМАНИЕ: ваниль {v:.2f} < 50 мс — сцена НЕ лагает, AC #5 не выполнен")
        print("\n  Порог успеха (AC #6) НЕ подтверждён пользователем -> это ЗАМЕРЕНО, не «успех».")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--runs", type=int, default=3, help="прогонов на руку")
    ap.add_argument("--side", type=int, default=80, help="сторона платформы")
    ap.add_argument("--mob", default="zombie", help="тип моба сцены (villager = НОВЫЙ ИИ Brain)")
    ap.add_argument("--step", type=int, default=1, help="шаг сетки: 2 = вдвое реже, меньше давка")
    ap.add_argument("--samples", type=int, default=3, help="сэмплов MSPT за прогон")
    ap.add_argument("--gap", type=float, default=6.0, help="пауза между сэмплами, с")
    ap.add_argument("--stabilize", type=float, default=20.0, help="стабилизация после постройки, с")
    ap.add_argument("--arms", default="vanilla,patched")
    args = ap.parse_args()

    MOB[0] = args.mob
    java = tp.find_java()
    pause_orig = tp.read_pause_property()
    _set_pause(0)
    print(f"pause-when-empty-seconds: {pause_orig} -> 0 на время замеров")
    arms = args.arms.split(",")
    # Чередование: V,P,V,P,... Дрейф машины бьёт по обеим рукам одинаково.
    order = [arm for _ in range(args.runs) for arm in arms]
    print(f"порядок прогонов: {order}")

    results = []
    for arm in order:
        try:
            results.append(
                run_once(arm, args.side, java, args.samples, args.gap, args.stabilize, args.step)
            )
        except Exception as e:
            print(f"  ПРОГОН УПАЛ ({arm}): {e}", flush=True)
            crash = sorted(CRASH_DIR.glob("crash-*.txt"), key=lambda p: p.stat().st_mtime)
            if crash and time.time() - crash[-1].stat().st_mtime < 300:
                head = crash[-1].read_text(encoding="utf-8", errors="replace").splitlines()[5:9]
                print(f"    свежий crash-report {crash[-1].name}:", flush=True)
                for ln in head:
                    print(f"      {ln}", flush=True)
            results.append(
                {
                    "arm": arm,
                    "mspt": [],
                    "median": 0.0,
                    "ok": False,
                    "errors": [str(e)],
                    "n_before": 0,
                    "n_after": 0,
                }
            )

    _set_pause(pause_orig)
    print(f"pause-when-empty-seconds восстановлен в {pause_orig}")
    return 0 if report(results, args.side) else 1


if __name__ == "__main__":
    sys.exit(main())
