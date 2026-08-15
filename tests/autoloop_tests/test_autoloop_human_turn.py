"""Кто именно вернулся в чат.

Отмена уборки шла по mtime транскрипта, а в него пишет и агент: каждый его шаг
читался как приход человека. На живом прогоне отмена случилась через две
секунды после старта фоновой работы, лог сказал «человек вернулся в чат» —
человека в комнате не было, — и стоила десяти минут карантина при заполненном
окне.
"""

import time

import autoloop_presence as presence

NOW = 1_800_000_000.0


def at(offset: float) -> str:
    """Отметка времени записи: `offset` секунд ДО условного «сейчас»."""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(NOW - offset, timezone.utc).isoformat().replace("+00:00", "Z")


def human(text="привет", offset=10.0, **extra):
    entry = {"type": "user", "timestamp": at(offset), "message": {"role": "user", "content": text}}
    entry.update(extra)
    return entry


def human_blocks(text="смотри сюда", offset=10.0):
    return {
        "type": "user",
        "timestamp": at(offset),
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def tool_result(offset=1.0):
    return {
        "type": "user",
        "timestamp": at(offset),
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "content": "ok", "tool_use_id": "x"}],
        },
    }


def assistant(offset=1.0):
    return {
        "type": "assistant",
        "timestamp": at(offset),
        "message": {"role": "assistant", "content": [{"type": "text", "text": "делаю"}]},
    }


# --- кто пишет запись -----------------------------------------------------


def test_a_typed_line_is_a_human_turn():
    assert presence.is_human_turn(human()) is True


def test_a_text_block_is_a_human_turn_too():
    """Сообщение с картинкой приходит списком блоков, а не строкой."""
    assert presence.is_human_turn(human_blocks()) is True


def test_a_tool_result_is_not_a_human_turn():
    """Несущий случай: в замеренном транскрипте 185 из 185 «пользовательских»
    записей были результатами инструментов."""
    assert presence.is_human_turn(tool_result()) is False


def test_the_agents_own_answer_is_not_a_human_turn():
    assert presence.is_human_turn(assistant()) is False


def test_a_sidechain_turn_is_not_the_human():
    """Субагент говорит в своей ветке; человек в ней не сидит."""
    assert presence.is_human_turn(human(isSidechain=True)) is False


def test_an_empty_line_is_not_a_turn():
    assert presence.is_human_turn(human(text="   ")) is False


# --- сколько человек молчит ------------------------------------------------


def test_silence_is_measured_from_the_humans_last_line(transcript):
    path = transcript([human(offset=120), assistant(offset=3), tool_result(offset=1)])

    quiet = presence.human_idle_seconds(path, now=NOW)

    assert 119 <= quiet <= 121, quiet  # работа агента отсчёт не сбрасывает


def test_a_human_line_resets_the_silence(transcript):
    """AC negative: настоящая реплика обязана возвращать отсчёт к нулю, иначе
    уборка пройдёт по человеку, который только что заговорил."""
    path = transcript([human(offset=300), assistant(offset=60), human(offset=2)])

    assert presence.human_idle_seconds(path, now=NOW) <= 3


def test_a_transcript_of_agent_work_alone_reads_as_long_silence(transcript):
    """Человеческих реплик нет вовсе — это не «не знаю», а «молчит дольше
    прочитанного куска»: файл читается, просто человек в нём не говорил."""
    path = transcript([assistant(offset=400), tool_result(offset=390), assistant(offset=5)])

    quiet = presence.human_idle_seconds(path, now=NOW)

    assert quiet is not None
    assert quiet >= 399, quiet


def test_a_missing_transcript_is_unknown_not_quiet():
    """AC negative: не знать — не значит «никого нет». Вызывающий обязан
    трактовать None как присутствие человека."""
    assert presence.human_idle_seconds(None) is None
    assert presence.human_idle_seconds("D:/nope/does-not-exist.jsonl") is None


def test_an_empty_transcript_is_unknown(transcript):
    assert presence.human_idle_seconds(transcript([]), now=NOW) is None


def test_only_the_tail_is_read(transcript):
    """Транскрипт живого прогона — мегабайты, а спрашивают каждые две секунды.
    Реплика человека в хвосте обязана находиться при крошечном окне чтения."""
    noise = [assistant(offset=200 - i * 0.01) for i in range(400)]
    path = transcript([human(offset=999), *noise, human(offset=4)])

    assert presence.human_idle_seconds(path, now=NOW, tail=4096) <= 5


# --- что из этого делает наблюдатель ---------------------------------------


def spin(monkeypatch, project_dir, *, human_quiet, ticks=3):
    """Прокрутить цикл наблюдателя с полным окном и молчащим файлом.

    Меняется одно: сколько молчит ЧЕЛОВЕК. Всё остальное — окно на 99%, тишина
    транскрипта, живой чат, неподвижный экран — закреплено.
    """
    import autoloop_chat_cycle as cycle_state
    import autoloop_watch as watch

    clock = iter([0.0, 10.0, 20.0, 30.0])
    beats = iter([True] * ticks + [False])
    ran = []
    cycle_state.start_run(str(project_dir), "очередь задач")
    monkeypatch.setattr(watch.keys, "pid_exists", lambda _pid: True)
    monkeypatch.setattr(watch, "alive", lambda _pid: next(beats))
    monkeypatch.setattr(watch, "current_percent", lambda _p: 99)
    monkeypatch.setattr(watch, "transcript_path", lambda *_a: "chat.jsonl")
    monkeypatch.setattr(watch, "idle_seconds", lambda *_a, **_k: 600.0)
    monkeypatch.setattr(watch, "human_idle_seconds", lambda *_a, **_k: human_quiet)
    monkeypatch.setattr(watch.keys, "console_text", lambda _pid: "> ")
    monkeypatch.setattr(watch.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(watch.time, "sleep", lambda _s: None)
    monkeypatch.setattr(watch, "run_sequence", lambda *_a: ran.append("sequence") or True)
    watch.watch(str(project_dir), pid=111, threshold=30)
    return ran, (project_dir / ".tausik" / "chat-watch.log").read_text(encoding="utf-8")


def test_agent_work_does_not_cancel_the_cleanup(project_dir, monkeypatch):
    """Сам дефект: человек молчит десять минут, агент всё это время работает.
    Раньше каждый его шаг читался как «человек вернулся» и отменял уборку."""
    ran, journal = spin(monkeypatch, project_dir, human_quiet=600.0)

    assert ran == ["sequence"]
    assert "отменено" not in journal


def test_a_human_line_still_cancels_the_cleanup(project_dir, monkeypatch):
    """AC negative: несущее свойство механизма. Человек заговорил — уборка
    отменяется, иначе она сотрёт разговор из-под него."""
    ran, journal = spin(monkeypatch, project_dir, human_quiet=2.0)

    assert ran == []
    assert "человек вернулся в чат" in journal


def test_an_unreadable_transcript_cancels_too(project_dir, monkeypatch):
    """AC negative: не знать — не значит «никого нет»."""
    ran, journal = spin(monkeypatch, project_dir, human_quiet=None)

    assert ran == []
    assert "человек вернулся в чат" in journal


def test_now_defaults_to_the_clock(transcript):
    """Без явного `now` функция обязана считать от текущего времени, иначе в
    бою она вернёт отрицательную или бессмысленную тишину."""
    from datetime import datetime, timezone

    stamp = datetime.fromtimestamp(time.time() - 30, timezone.utc).isoformat()
    path = transcript(
        [{"type": "user", "timestamp": stamp, "message": {"role": "user", "content": "ау"}}]
    )

    quiet = presence.human_idle_seconds(path)

    assert quiet is not None and 25 <= quiet <= 40, quiet
