"""Доказать, что тик ванильного сервера ИДЁТ — до того, как верить числам профиля.

Ловушка (memory #35): ванильный сервер останавливает тик без игроков
(«Server empty for 60 seconds, pausing»). Профилировщик на паузе не падает — он
отдаёт правдоподобные нули по блок-энтити. Ошибка выглядит как результат.

Зонд меряет gametime: он растёт РОВНО НА ЕДИНИЦУ ЗА ТИК, поэтому дельта gametime
за известное wall-время — прямой счётчик тиков, а не косвенный признак.

Прогоняются ОБЕ руки:
  control  — pause-when-empty-seconds=60 (дефолт): ловушка ОБЯЗАНА сработать
  treatment— pause-when-empty-seconds=0:           тик обязан идти

Контроль здесь — намеренная мутация (convention #22). Зонд, который не умеет
показать ловушку, не доказывает и её отсутствия: оба исхода выглядели бы как
«числа получены».

Использование:
    python tick_probe.py                 # обе руки
    python tick_probe.py --arm treatment # одна
    python tick_probe.py --observe 90    # окно наблюдения, сек (по умолчанию 90)
"""

from __future__ import annotations

import argparse
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER_DIR = HERE / "server"
JAR = SERVER_DIR / "minecraft_server.26.2.jar"
PROPS = SERVER_DIR / "server.properties"
PAUSE_KEY = "pause-when-empty-seconds"

# Окно по умолчанию — 90 с: заведомо больше дефолтных 60, иначе контрольная рука
# не успеет дойти до паузы и «пройдёт» по недосмотру, а не по существу.
DEFAULT_OBSERVE_S = 90
STARTUP_TIMEOUT_S = 180
TICKS_PER_SECOND = 20.0
SAMPLE_INTERVAL_S = 5.0

# Вердикт строится на ХВОСТЕ окна, а не на среднем по всему окну. Первая версия
# усредняла всё окно и провалилась на арифметике: контроль тикает 60 с из 90, что
# даёт 13.3 TPS — выше любого разумного порога «мир стоит», хотя мир стоит уже
# полминуты. Среднее отвечает на «сколько тиков прошло за окно», а вопрос AC #2 —
# «идёт ли тик СЕЙЧАС». Хвост отвечает именно на него.
TAIL_WINDOW_S = 20.0

RE_DONE = re.compile(r'Done \([\d.]+s\)! For help, type "help"')
# Формат сверен с assets/minecraft/lang/en_us.json в jar, а не угадан. Ловушка:
# commands.time.query = 'The time is %s' — это DAYTIME, другая команда. Шаблон
# «The time is (\d+)» совпал бы с ней и вернул время суток вместо счётчика тиков
# (а оно стоит при doDaylightCycle=false) — тихое неверное число вместо падения.
RE_GAMETIME = re.compile(r"The game time is (\d+) tick\(s\)")  # commands.time.query.gametime
RE_PAUSING = re.compile(r"Server empty for (\d+) seconds, pausing")
RE_TICK_STATUS = re.compile(
    r"The game is (running normally|frozen|sprinting)|"
    r"The game is (running), but can't keep up"
)
RE_AVG_TICK = re.compile(r"Average time per tick: ([\d.]+)ms")


def find_java() -> Path:
    """JDK 25 из jre/ (26.2 требует именно 25; системные 17/21/24 не годятся)."""
    candidates = sorted((HERE / "jre").glob("jdk-*/bin/java.exe"))
    if not candidates:
        sys.exit(f"java не найдена в {HERE / 'jre'} — запусти setup_mc_server.py --provision-java")
    return candidates[0]


@dataclass
class ArmResult:
    arm: str
    pause_setting: int
    samples: list[tuple[float, int]]  # (секунда наблюдения, gametime)
    pausing_seen: bool
    tick_status: str = "?"
    avg_tick_ms: float = 0.0

    @property
    def observed_s(self) -> float:
        return self.samples[-1][0] - self.samples[0][0]

    @property
    def ticks(self) -> int:
        return self.samples[-1][1] - self.samples[0][1]

    @property
    def observed_tps(self) -> float:
        return self.ticks / self.observed_s if self.observed_s else 0.0

    @property
    def expected_ticks(self) -> int:
        return int(self.observed_s * TICKS_PER_SECOND)

    @property
    def tail_tps(self) -> float:
        """TPS на последних TAIL_WINDOW_S секундах — «идёт ли тик СЕЙЧАС»."""
        cutoff = self.samples[-1][0] - TAIL_WINDOW_S
        tail = [s for s in self.samples if s[0] >= cutoff]
        if len(tail) < 2:
            return self.observed_tps
        dt = tail[-1][0] - tail[0][0]
        return (tail[-1][1] - tail[0][1]) / dt if dt else 0.0


class ServerProc:
    """Сервер + чтение stdout в фоне. Команды идут в stdin без ведущего слэша."""

    def __init__(self, java: Path):
        self.lines: queue.Queue[str] = queue.Queue()
        self.transcript: list[str] = []
        self.proc = subprocess.Popen(
            [str(java), "-Xmx4G", "-jar", JAR.name, "nogui"],
            cwd=str(SERVER_DIR),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            self.transcript.append(line)
            self.lines.put(line)

    def send(self, cmd: str) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(cmd + "\n")
        self.proc.stdin.flush()

    def await_line(self, pattern: re.Pattern, timeout: float) -> re.Match | None:
        """Ждать строку по шаблону. Возвращает None по таймауту — молча не врёт."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=0.2)
            except queue.Empty:
                continue
            m = pattern.search(line)
            if m:
                return m
        return None

    def query_gametime(self, timeout: float = 15.0) -> int:
        self.send("time query gametime")
        m = self.await_line(RE_GAMETIME, timeout)
        if not m:
            raise RuntimeError("сервер не ответил на 'time query gametime'")
        return int(m.group(1))

    def query_tick(self, timeout: float = 15.0) -> tuple[str, float]:
        """Статус и среднее время тика — свидетельство, независимое от gametime.

        Нужно именно второе: gametime отвечает «сколько тиков», /tick query —
        «в каком режиме сервер их делает». Пауза и штатная работа различаются
        обоими, но по-разному, и расхождение между ними — сигнал, что зонд врёт.
        """
        self.send("tick query")
        m_status = self.await_line(RE_TICK_STATUS, timeout)
        status = next((g for g in m_status.groups() if g), "?") if m_status else "?"
        m_avg = self.await_line(RE_AVG_TICK, 5.0)
        return status, float(m_avg.group(1)) if m_avg else 0.0

    def stop(self) -> None:
        try:
            self.send("stop")
            self.proc.wait(timeout=60)
        except Exception:
            self.proc.kill()


def set_pause_property(value: int) -> None:
    """Переписать ОДИН ключ, сохранив остальной файл байт-в-байт."""
    text = PROPS.read_text(encoding="utf-8")
    new, n = re.subn(rf"^{PAUSE_KEY}=.*$", f"{PAUSE_KEY}={value}", text, flags=re.M)
    if n != 1:
        raise RuntimeError(f"ожидалось ровно одно вхождение {PAUSE_KEY}, найдено {n}")
    PROPS.write_text(new, encoding="utf-8")


def read_pause_property() -> int:
    m = re.search(rf"^{PAUSE_KEY}=(\d+)$", PROPS.read_text(encoding="utf-8"), flags=re.M)
    if not m:
        raise RuntimeError(f"{PAUSE_KEY} не найден в {PROPS}")
    return int(m.group(1))


def run_arm(arm: str, pause_setting: int, observe_s: float, java: Path) -> ArmResult:
    print(f"\n{'=' * 62}\n  РУКА: {arm}  ({PAUSE_KEY}={pause_setting})\n{'=' * 62}")
    set_pause_property(pause_setting)

    srv = ServerProc(java)
    try:
        if not srv.await_line(RE_DONE, STARTUP_TIMEOUT_S):
            raise RuntimeError("сервер не поднялся за отведённое время")
        print("  сервер поднят")

        t0 = time.monotonic()
        samples: list[tuple[float, int]] = [(0.0, srv.query_gametime())]
        print(f"  gametime старт: {samples[0][1]}")
        print(
            f"  наблюдаю {observe_s:.0f} с, отсчёт каждые {SAMPLE_INTERVAL_S:.0f} с"
            f" (порог паузы дефолта — 60 с)..."
        )

        while (elapsed := time.monotonic() - t0) < observe_s:
            time.sleep(min(SAMPLE_INTERVAL_S, observe_s - elapsed))
            samples.append((time.monotonic() - t0, srv.query_gametime()))

        status, avg_ms = srv.query_tick()
        print(f"  gametime конец: {samples[-1][1]}")
        print(f"  tick query: статус={status!r}, среднее={avg_ms} мс")

        pausing = any(RE_PAUSING.search(ln) for ln in srv.transcript)
        return ArmResult(arm, pause_setting, samples, pausing, status, avg_ms)
    finally:
        srv.stop()


def report(results: list[ArmResult]) -> bool:
    print(f"\n{'=' * 62}\n  ИТОГ\n{'=' * 62}")
    print(
        f"{'рука':<11}{'pause':>6}{'тиков':>9}{'ожид.':>9}{'TPS ср':>8}{'TPS хвост':>11}"
        f"{'мс/тик':>9}  {'pausing':<8}{'tick query'}"
    )
    for r in results:
        print(
            f"{r.arm:<11}{r.pause_setting:>6}{r.ticks:>9}{r.expected_ticks:>9}"
            f"{r.observed_tps:>8.2f}{r.tail_tps:>11.2f}{r.avg_tick_ms:>9.2f}  "
            f"{'ДА' if r.pausing_seen else 'нет':<8}{r.tick_status}"
        )
    print(f"\n  «TPS хвост» — последние {TAIL_WINDOW_S:.0f} с. Вердикты строятся на нём.")
    print("  «tick query» намеренно оставлен в таблице: он показывает ОДНО И ТО ЖЕ")
    print("  на обеих руках и потому доказательством служить не может (memory #41).")

    by_arm = {r.arm: r for r in results}
    ok = True

    if "control" in by_arm:
        c = by_arm["control"]
        # Контроль обязан ВЛЕТЕТЬ в ловушку. Если он тикает и в хвосте — ловушки
        # на этой машине нет, и «успех» лечения ничего не значит.
        trapped = c.pausing_seen and c.tail_tps < TICKS_PER_SECOND * 0.1
        print(f"\n  control  — ловушка {'ВОСПРОИЗВЕДЕНА' if trapped else 'НЕ воспроизведена'}")
        if not trapped:
            print("    контроль не попал в паузу => зонд не доказывает ничего.")
            ok = False

    if "treatment" in by_arm:
        t = by_arm["treatment"]
        # Порог 0.9: тик должен идти ПОЧТИ на полной скорости. Просто «>0» не
        # годится — подтормаживающий сервер тоже даёт >0 и тоже врёт в профиле.
        healthy = not t.pausing_seen and t.tail_tps >= TICKS_PER_SECOND * 0.9
        print(f"  treatment— тик {'ИДЁТ' if healthy else 'НЕ идёт как надо'}")
        if not healthy:
            print("    тик не подтверждён => числа профиля брать НЕЛЬЗЯ (AC #2).")
            ok = False

    print(f"\n  ВЕРДИКТ: {'AC #2 подтверждён' if ok else 'AC #2 НЕ подтверждён'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--arm", choices=["control", "treatment", "both"], default="both")
    ap.add_argument("--observe", type=float, default=DEFAULT_OBSERVE_S, help="окно наблюдения, сек")
    args = ap.parse_args()

    if not JAR.exists():
        sys.exit(f"нет {JAR} — запусти setup_mc_server.py")
    # Хвост обязан лежать ЦЕЛИКОМ за порогом паузы (60 с). Иначе в него попадут
    # тики, снятые ДО паузы, контроль размажется и «пройдёт» по недосмотру —
    # ровно тот баг, из-за которого первый прогон дал ложное «НЕ воспроизведена».
    min_observe = 60 + TAIL_WINDOW_S
    if args.observe < min_observe:
        sys.exit(
            f"--observe={args.observe} < {min_observe:.0f}: хвост ({TAIL_WINDOW_S:.0f} с) "
            f"должен целиком лежать за порогом паузы (60 с)"
        )

    java = find_java()
    original = read_pause_property()
    print(f"java: {java}\n{PAUSE_KEY} на входе: {original} (будет восстановлен)")

    arms = (
        [("control", 60), ("treatment", 0)]
        if args.arm == "both"
        else [(args.arm, 60 if args.arm == "control" else 0)]
    )

    results: list[ArmResult] = []
    try:
        for name, setting in arms:
            results.append(run_arm(name, setting, args.observe, java))
    finally:
        set_pause_property(original)
        print(f"\n{PAUSE_KEY} восстановлен в {original}")

    return 0 if report(results) else 1


if __name__ == "__main__":
    sys.exit(main())
