"""Уборка контекста по просьбе человека.

Механизм чистит сам, когда сойдутся три условия: окно полное, чат молчит 45
секунд, карантин после отказа кончился. Здесь проверяется вторая дверь — та, в
которую человек стучит сам. Она обязана открываться, когда автоматическая
закрыта, и обязана НЕ открываться, пока идёт фоновая работа.
"""

from itertools import chain, repeat

import autoloop_chat_cycle as cycle_state
import autoloop_clean_request as clean_request
import autoloop_command as command
import autoloop_run_state as run_state
import autoloop_watch as watch

from .conftest import SKILLS_DIR


def _flags(busy):
    """Занятость по тикам: одно значение — навсегда, список — по порядку, а
    дальше держится последнее."""
    values = list(busy) if isinstance(busy, (list, tuple)) else [busy]
    return chain(values, repeat(values[-1]))


def spin(
    monkeypatch,
    project_dir,
    *,
    percent=5,
    quiet=0.0,
    human_quiet=None,
    busy=False,
    ticks=2,
    request_on_tick=None,
    sequence=None,
):
    """Прогнать цикл наблюдателя несколько тиков.

    По умолчанию всё, на чём стоит автоматическая уборка, выключено: окно почти
    пустое, а человек говорил только что. Значит любая сработавшая уборка —
    следствие запроса, а не совпадения.
    """
    clock = iter([float(n) * 10 for n in range(ticks + 3)])
    beats = iter([True] * ticks + [False])
    tick = iter(range(1, ticks + 2))
    occupied = _flags(busy)
    ran = []
    cycle_state.start_run(str(project_dir), "очередь задач")

    def percent_now(_project):
        # Момент, когда человек просит уборку: между тиками, а не до первого —
        # иначе запрос сработает раньше, чем цикл успеет во что-нибудь войти.
        if next(tick) == request_on_tick:
            clean_request.request(str(project_dir))
        return percent

    monkeypatch.setattr(watch.keys, "pid_exists", lambda _pid: True)
    monkeypatch.setattr(watch, "alive", lambda _pid: next(beats))
    monkeypatch.setattr(watch, "current_percent", percent_now)
    monkeypatch.setattr(watch, "transcript_path", lambda *_a: "chat.jsonl")
    monkeypatch.setattr(watch, "idle_seconds", lambda *_a, **_k: quiet)
    monkeypatch.setattr(
        watch, "human_idle_seconds", lambda *_a, **_k: quiet if human_quiet is None else human_quiet
    )
    monkeypatch.setattr(watch.keys, "console_text", lambda _pid: "> ")
    monkeypatch.setattr(watch.keys, "process_table", dict)
    monkeypatch.setattr(
        watch, "background_pids", lambda *_a, **_k: {999} if next(occupied) else set()
    )
    monkeypatch.setattr(watch.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(watch.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        watch, "run_sequence", sequence or (lambda *_a: ran.append("sequence") or True)
    )
    watch.watch(str(project_dir), pid=111, threshold=30)
    return ran, (project_dir / ".tausik" / "chat-watch.log").read_text(encoding="utf-8")


# --- чего просьба не спрашивает --------------------------------------------


def test_a_request_cleans_a_window_nobody_would_have_cleaned(project_dir, monkeypatch):
    """AC: оба условия автоматической уборки отсутствуют — окно на 5% и человек
    писал секунду назад, — а уборка всё равно проходит. Именно за этим команду и
    зовут: человек видит окно и решает сам, порог и тишина были догадкой о нём."""
    clean_request.request(str(project_dir))

    ran, journal = spin(monkeypatch, project_dir, percent=5, quiet=0.0)

    assert ran == ["sequence"]
    assert "уборка по просьбе человека" in journal


def test_a_request_cleans_a_full_window_too(project_dir, monkeypatch):
    """Просьба — не «вместо» автоматики, а «поверх»: в состоянии, где сработала
    бы и она, ждать пятнадцати секунд взведения незачем."""
    clean_request.request(str(project_dir))

    ran, _journal = spin(monkeypatch, project_dir, percent=99, quiet=0.0)

    assert ran == ["sequence"]


def test_a_refusal_in_cooldown_does_not_hold_the_request(project_dir, monkeypatch):
    """AC: отказ держит автоматику десять минут (CANCEL_QUIET). Просьба этот
    карантин не ждёт — он отвечает на «человек сказал нет», а это человек
    говорит да. Карантин здесь настоящий: первый тик взводит уборку на полном
    окне, вторая тишина отменяет её, и только потом приходит запрос."""
    ran, journal = spin(
        monkeypatch,
        project_dir,
        percent=99,
        quiet=600.0,
        human_quiet=0.0,
        request_on_tick=2,
        ticks=3,
    )

    assert "отменено: человек вернулся в чат" in journal  # карантин действительно выставлен
    assert ran == ["sequence"]


# --- чего просьба не отменяет ----------------------------------------------


def test_work_still_running_holds_the_request(project_dir, monkeypatch):
    """AC негативный: `/clear` посреди фоновой работы теряет незавершённое, а
    человек, который просит уборку, смотрит на окно, а не на текущий шаг агента
    — он не знает, что прерывает. Единственное условие, которое просьба не
    снимает."""
    clean_request.request(str(project_dir))

    ran, journal = spin(monkeypatch, project_dir, busy=True)

    assert ran == []
    assert clean_request.requested(str(project_dir))  # запрос дождётся своего тика
    assert "уборка по просьбе ждёт конца фоновой работы" in journal


def test_the_request_survives_until_the_work_is_gone(project_dir, monkeypatch):
    """«Отложить» обязано означать «провести позже», а не «забыть»: запрос,
    молча пропавший вместе с фоновой работой, неотличим для человека от
    уборки, которая просто не случилась."""
    clean_request.request(str(project_dir))

    ran, _journal = spin(monkeypatch, project_dir, busy=(True, False), ticks=3)

    assert ran == ["sequence"]


# --- одноразовость ----------------------------------------------------------


def test_the_request_is_gone_before_the_first_keystroke(project_dir, monkeypatch):
    """AC: уборка может умереть на середине — консоль перестала читать, след
    `/checkpoint` не пришёл, прогон сняли посреди цикла. Снятый ПОСЛЕ запрос
    переживёт любой из этих исходов, и следующий тик начнёт всё заново."""
    clean_request.request(str(project_dir))
    seen = []

    spin(
        monkeypatch,
        project_dir,
        sequence=lambda *_a: seen.append(clean_request.requested(str(project_dir))) or True,
    )

    assert seen == [False]


def test_one_request_is_one_cleanup(project_dir, monkeypatch):
    """Запрос, переживший уборку, повторит её на следующем тике — и на
    следующем, и так, пока жив наблюдатель."""
    clean_request.request(str(project_dir))

    ran, _journal = spin(monkeypatch, project_dir, ticks=4)

    assert ran == ["sequence"]


# --- команда ----------------------------------------------------------------


def test_without_a_run_the_command_refuses_and_leaves_nothing(project_dir):
    """AC негативный: наблюдатель существует только внутри объявленного
    прогона. Файл, оставленный вне прогона, никто не прочитает — он пролежит до
    следующего `/auto` и сработает внезапно, посреди чужого разговора."""
    answer = command.clean(str(project_dir))

    assert "прогона нет" in answer
    assert not clean_request.requested(str(project_dir))


def test_agents_mode_has_no_chat_context_to_clean(project_dir):
    """У агентов каждая итерация приходит с чистым контекстом — чистить нечего,
    и чат к их прогону не подключён."""
    (project_dir / ".tausik" / ".autoloop.run").write_text("", encoding="utf-8")

    answer = command.clean(str(project_dir))

    assert run_state.mode(str(project_dir)) == run_state.MODE_AGENTS
    assert "агентами" in answer
    assert not clean_request.requested(str(project_dir))


def test_a_dead_watcher_is_named_rather_than_left_to_be_guessed(project_dir, monkeypatch):
    """AC негативный: прогон объявлен, а процесс не поднят. Запрос остаётся —
    наблюдатель поднимется на старте сессии и заберёт его, — но молчать об этом
    нельзя: человек будет ждать уборки, которая не начнётся."""
    cycle_state.start_run(str(project_dir), "очередь задач")
    monkeypatch.setattr(command.watch, "alive", lambda _pid: False)

    answer = command.clean(str(project_dir))

    assert "наблюдатель не поднят" in answer
    assert clean_request.requested(str(project_dir))


def test_a_live_watcher_promises_the_next_tick_and_names_the_one_wait(project_dir, monkeypatch):
    """AC: ответ обязан сказать про фоновую работу. Сама команда занятость не
    определяет — она запущена из чата и своим же процессом попала бы в
    `background_pids`, отвечая «занят» всегда."""
    cycle_state.start_run(str(project_dir), "очередь задач")
    monkeypatch.setattr(command.watch, "alive", lambda _pid: True)

    answer = command.clean(str(project_dir))

    assert clean_request.requested(str(project_dir))
    assert "/checkpoint" in answer and "/clear" in answer
    assert "фоновая работа" in answer


def test_a_request_does_not_outlive_the_run_it_was_left_for(project_dir):
    """AC негативный, вторая его половина: прогон можно снять, пока запрос ждёт
    конца фоновой работы. Оставленный лежать, он достанется первому тику
    СЛЕДУЮЩЕГО прогона и почистит разговор, владелец которого ни о чём не
    просил — то самое «внезапно», ради которого команда отказывает без прогона."""
    cycle_state.start_run(str(project_dir), "очередь задач")
    clean_request.request(str(project_dir))

    command.stop(str(project_dir))

    assert not clean_request.requested(str(project_dir))


def test_a_forgotten_request_is_not_inherited_by_the_next_run(project_dir, monkeypatch):
    """Прогон мог кончиться не командой: наблюдатель умер с запросом на руках,
    файл прогона удалили руками. Объявление нового прогона забирает наследство
    себе, а не начинается с чужой уборки."""
    clean_request.request(str(project_dir))
    monkeypatch.setattr(command.window, "spawn_overlay", lambda _dir: "")
    monkeypatch.setattr(command, "chat_pid", lambda: None)

    command.start(str(project_dir), "очередь задач")

    assert not clean_request.requested(str(project_dir))


# --- скилл ------------------------------------------------------------------


def test_the_skill_routes_the_words_a_person_actually_says():
    """Ветка выбирается словом в аргументе. Слова уборки должны стоять в
    таблице ВЫШЕ строки «любой другой текст» — иначе «почисти» уедет в запуск
    прогона с направлением «почисти»."""
    skill = (SKILLS_DIR / "auto" / "SKILL.md").read_text(encoding="utf-8")
    rows = [line for line in skill.splitlines() if line.startswith("|")]
    cleaning = [n for n, line in enumerate(rows) if "почисти" in line]
    catch_all = [n for n, line in enumerate(rows) if "любой другой текст" in line]

    assert cleaning, "слова уборки не названы в таблице разбора"
    assert catch_all and cleaning[0] < catch_all[0], "строка уборки ниже общего случая"
    assert "autoloop_command.py clean" in skill
