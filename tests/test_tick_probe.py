"""Логика вердикта зонда тика MC (async-platform/mc/tick_probe.py).

Регрессия на две ошибки, пойманные живьём в mc-tick-profile. Обе — один класс:
правдоподобное число вместо громкого отказа, то есть memory #27/#35/#41.

1. Вердикт усреднял TPS по всему окну. Контроль тикает 60 с из 90 и встаёт, что
   даёт 13.3 TPS — выше любого порога «мир стоит», хотя мир стоит полминуты.
2. Регексп gametime был выдуман: «The time is %s» — формат ДРУГОЙ команды
   (daytime). При описке daytime/gametime он бы СОВПАЛ и вернул время суток
   вместо счётчика тиков.

Имя файла повторяет имя модуля: gate_test_resolver мапит scoped pytest по
basename (см. tests/test_backend_roadmap.py).

Модуль грузится по пути, а не импортом: async-platform/mc не лежит в
pythonpath (pyproject: pythonpath = ["scripts"]), и добавлять его туда ради
одного теста — менять общий конфиг фреймворка.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "async-platform" / "mc" / "tick_probe.py"


@pytest.fixture(scope="module")
def tp():
    spec = importlib.util.spec_from_file_location("tick_probe_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    # dataclass резолвит аннотации через sys.modules[cls.__module__]; без
    # регистрации падает с AttributeError на ровном месте.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    del sys.modules[spec.name]


def _samples(
    stop_at_s: float | None, observe_s: float = 90.0, step: float = 5.0, start_gt: int = 1000
):
    """Отсчёты (секунда, gametime). stop_at_s=None — мир тикает всё окно."""
    out = []
    t = 0.0
    while t <= observe_s:
        ticking_s = t if stop_at_s is None else min(t, stop_at_s)
        out.append((t, start_gt + int(ticking_s * 20)))
        t += step
    return out


def _arm(tp, name, pause, samples, pausing):
    return tp.ArmResult(name, pause, samples, pausing, "running normally", 0.1)


def test_tail_tps_is_zero_when_world_stopped(tp):
    """Контроль: мир встал на 60-й секунде -> хвост обязан быть ровно 0."""
    arm = _arm(tp, "control", 60, _samples(stop_at_s=60.0), True)
    assert arm.tail_tps == 0.0


def test_tail_tps_is_full_when_world_runs(tp):
    arm = _arm(tp, "treatment", 0, _samples(stop_at_s=None), False)
    assert arm.tail_tps == pytest.approx(20.0, abs=0.1)


def test_average_tps_hides_the_pause(tp):
    """Регрессия. Ради этого числа вердикт и переехал на хвост.

    Среднее по окну даёт 13.3 при МЁРТВОМ мире — оно отвечает на «сколько тиков
    прошло за окно», а вопрос AC #2 — «идёт ли тик СЕЙЧАС». Тест пинит именно
    расхождение: если кто-то вернёт вердикт на observed_tps, здесь станет видно,
    что порог «< 10» на этом числе не срабатывает.
    """
    arm = _arm(tp, "control", 60, _samples(stop_at_s=60.0), True)
    assert arm.observed_tps == pytest.approx(13.3, abs=0.2)
    assert arm.observed_tps > 10.0  # старый порог «мир стоит» ЗДЕСЬ НЕ СРАБОТАЛ
    assert arm.tail_tps == 0.0  # а хвост говорит правду


def test_tail_falls_back_when_samples_too_few(tp):
    """Один отсчёт в хвосте — считать нечего; молча делить на ноль нельзя."""
    arm = _arm(tp, "treatment", 0, [(0.0, 1000), (90.0, 2800)], False)
    assert arm.tail_tps == arm.observed_tps


def test_gametime_regex_rejects_daytime_format(tp):
    """memory #38: «The time is %s» — это daytime, НЕ счётчик тиков.

    Формат сверен с assets/minecraft/lang/en_us.json в server-26.2.jar:
      commands.time.query.gametime = 'The game time is %s tick(s)'
      commands.time.query          = 'The time is %s'
    Совпадение со вторым дало бы время суток вместо тиков — а оно СТОИТ при
    doDaylightCycle=false, то есть тихий ноль вместо падения.
    """
    assert tp.RE_GAMETIME.search("[00:34:04] [Server thread/INFO]: The time is 1200") is None
    m = tp.RE_GAMETIME.search("[00:34:04] [Server thread/INFO]: The game time is 1200 tick(s)")
    assert m is not None and m.group(1) == "1200"


def test_pausing_regex_matches_observed_log_line(tp):
    """Строка снята из живого лога 26.2, а не сочинена."""
    assert tp.RE_PAUSING.search(
        "[00:35:04] [Server thread/INFO]: Server empty for 60 seconds, pausing"
    )


def test_tail_window_lies_entirely_past_the_pause_threshold(tp):
    """Гард на --observe: хвост, задевающий тики ДО паузы, размажет контроль."""
    assert tp.TAIL_WINDOW_S > 0
    assert tp.DEFAULT_OBSERVE_S >= 60 + tp.TAIL_WINDOW_S
