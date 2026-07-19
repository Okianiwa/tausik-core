"""Держать сервер живым и слать в него команды из файла — пока на нём играет человек.

Зачем не RCON: он требует открыть порт и завести пароль на машине пользователя
ради временного замера. Здесь хватает stdin, а ответы и так идут в консоль.

Зачем вообще: /perf нужно запускать В МОМЕНТ, когда игрок уже в мире и занят
делом. Заранее заданный сценарий тут не годится — человек не бот.

Использование:
    python mc_session.py                      # поднять и ждать команд
    echo "perf start" >> <cmdfile>            # из другого окна/агента
    echo "__quit__"   >> <cmdfile>            # корректно остановить сервер

Файл команд по умолчанию — mc_cmd.txt рядом со скриптом. Скрипт читает ТОЛЬКО
дописанные строки: файл не перечитывается с начала, поэтому повторно слать одно
и то же безопасно.

ВАЖНО (memory #35): pause-when-empty-seconds выставляется в 0 на время сессии.
С игроком онлайн пауза и так не грозит, но если игрока ВЫКИНЕТ посреди замера,
дефолтные 60 с тихо остановят мир — и /perf намеряет паузу, не упав. Исходное
значение восстанавливается при выходе.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CMDFILE = HERE / "mc_cmd.txt"
POLL_S = 0.4
QUIT_TOKEN = "__quit__"


def _load_tick_probe():
    """ServerProc/find_java живут в tick_probe — второй запускатель не нужен."""
    spec = importlib.util.spec_from_file_location("tick_probe", HERE / "tick_probe.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["tick_probe"] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--cmdfile", type=Path, default=DEFAULT_CMDFILE)
    ap.add_argument("--max-minutes", type=float, default=90.0, help="страховка от забытого сервера")
    args = ap.parse_args()

    tp = _load_tick_probe()
    java = tp.find_java()

    args.cmdfile.write_text("", encoding="utf-8")  # начинаем с чистого файла команд
    original_pause = tp.read_pause_property()
    tp.set_pause_property(0)

    print(f"java: {java}")
    print(f"файл команд: {args.cmdfile}")
    print(f"pause-when-empty-seconds: {original_pause} -> 0 (вернётся в {original_pause})")

    srv = tp.ServerProc(java)
    deadline = time.monotonic() + args.max_minutes * 60
    sent = 0
    try:
        if not srv.await_line(tp.RE_DONE, tp.STARTUP_TIMEOUT_S):
            raise RuntimeError("сервер не поднялся")
        print("\n=== СЕРВЕР ПОДНЯТ — можно заходить на localhost:25565 ===\n", flush=True)

        with args.cmdfile.open("r", encoding="utf-8") as fh:
            while time.monotonic() < deadline:
                line = fh.readline()
                if not line:
                    # Консоль печатаем здесь же: так фоновый вывод скрипта
                    # показывает и ответы сервера, и наши команды в одном месте.
                    while not srv.lines.empty():
                        print("  |", srv.lines.get_nowait(), flush=True)
                    time.sleep(POLL_S)
                    continue
                cmd = line.strip()
                if not cmd or cmd.startswith("#"):
                    continue
                if cmd == QUIT_TOKEN:
                    print(">>> получен __quit__, останавливаю сервер", flush=True)
                    break
                print(f">>> {cmd}", flush=True)
                srv.send(cmd)
                sent += 1
            else:
                print(f">>> вышло время (--max-minutes {args.max_minutes})", flush=True)
    finally:
        srv.stop()
        tp.set_pause_property(original_pause)
        print(f"\nкоманд отправлено: {sent}")
        print(f"pause-when-empty-seconds восстановлен в {original_pause}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
