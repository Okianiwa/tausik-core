"""Что наблюдатель говорит о себе — и когда честнее не говорить ничего."""

from __future__ import annotations

import json
import time

import autoloop_watch_state as wstate


class TestWhichStateItIsIn:
    def test_blindness_outranks_everything(self):
        """Не видя чат, наблюдатель не действует вообще — остальные признаки в
        этом состоянии уже ничего не решают."""
        phase = wstate.phase_of(blind=True, busy=True, arming=True, percent=99, threshold=30)

        assert phase == wstate.PHASE_BLIND

    def test_background_work_outranks_the_countdown(self):
        phase = wstate.phase_of(blind=False, busy=True, arming=True, percent=99, threshold=30)

        assert phase == wstate.PHASE_BUSY

    def test_over_the_threshold_but_talking_is_waiting(self):
        phase = wstate.phase_of(blind=False, busy=False, arming=False, percent=42, threshold=30)

        assert phase == wstate.PHASE_WAITING

    def test_below_the_threshold_is_just_watching(self):
        phase = wstate.phase_of(blind=False, busy=False, arming=False, percent=12, threshold=30)

        assert phase == wstate.PHASE_WATCHING

    def test_no_reading_is_not_a_reason_to_act(self):
        """NEGATIVE: неизвестный процент — не «за порогом»."""
        phase = wstate.phase_of(blind=False, busy=False, arming=False, percent=None, threshold=30)

        assert phase == wstate.PHASE_WATCHING


class TestWhatItSays:
    def test_the_reason_is_named_not_the_verdict(self):
        """«Уборки не будет» без причины лишь передвигает вопрос на шаг."""
        assert "не вижу чат" in wstate.phrase(wstate.PHASE_BLIND)
        assert "фоновую работу" in wstate.phrase(wstate.PHASE_BUSY, workers=3)
        assert "3 проц." in wstate.phrase(wstate.PHASE_BUSY, workers=3)
        assert "отменю" in wstate.phrase(wstate.PHASE_ARMING)

    def test_the_waited_for_process_is_named(self):
        """Замер: «1 проц.» отправил человека искать агента, которого нет, — и
        так дважды за один прогон, пока ждали воркер индексатора графа."""
        assert wstate.phrase(wstate.PHASE_BUSY, workers=1, worker="codebase-memory-mcp") == (
            "жду фоновую работу: codebase-memory-mcp"
        )

    def test_several_processes_name_one_and_count_the_rest(self):
        line = wstate.phrase(wstate.PHASE_BUSY, workers=3, worker="python")

        assert "python" in line
        assert "+2" in line

    def test_a_long_name_does_not_stretch_the_plaque(self):
        """NEGATIVE: плашка подгоняет ширину под текст, поэтому длинное имя
        разъедет окно поверх чужих окон."""
        line = wstate.phrase(wstate.PHASE_BUSY, workers=1, worker="x" * 80)

        assert len(line) == len("жду фоновую работу: ") + wstate.MAX_WORKER_NAME

    def test_waiting_says_how_long_it_has_been_quiet(self):
        assert wstate.phrase(wstate.PHASE_WAITING, quiet=12.7) == "жду тишины · 12 с"

    def test_watching_names_both_numbers(self):
        assert wstate.phrase(wstate.PHASE_WATCHING, percent=12.5, threshold=30) == (
            "смотрю: 12.5% из 30%"
        )

    def test_a_missing_number_does_not_print_none(self):
        """NEGATIVE: «смотрю: None% из None%» — строка, которую нельзя показать."""
        text = wstate.phrase(wstate.PHASE_WATCHING, percent=None, threshold=None)

        assert text == "смотрю"
        assert "None" not in text


class TestSayingNothing:
    def test_a_fresh_state_round_trips(self, project_dir):
        wstate.observe(
            str(project_dir),
            blind=False,
            busy=False,
            arming=False,
            percent=27.6,
            threshold=30,
            quiet=4,
        )

        state = wstate.read(str(project_dir))

        assert state["phase"] == wstate.PHASE_WATCHING
        assert "27.6%" in state["detail"]

    def test_a_state_nobody_refreshed_is_not_the_present(self, project_dir):
        """NEGATIVE: тот же дефект, что с указателем на транскрипт — значение
        остаётся правдоподобным и относится к прошлому. Наблюдателя уже нет, а
        окно рассказывало бы, чего он ждёт."""
        wstate.observe(str(project_dir), blind=False, busy=True, arming=False, workers=2)
        path = project_dir / ".tausik" / ".chat-watch.state.json"
        stale = json.loads(path.read_text(encoding="utf-8"))
        stale["ts"] = time.time() - (wstate.MAX_AGE + 30)
        path.write_text(json.dumps(stale), encoding="utf-8")

        assert wstate.read(str(project_dir)) is None

    def test_no_file_is_no_state(self, project_dir):
        assert wstate.read(str(project_dir)) is None

    def test_a_corrupt_file_is_no_state(self, project_dir):
        path = project_dir / ".tausik" / ".chat-watch.state.json"
        path.write_text("{не json", encoding="utf-8")

        assert wstate.read(str(project_dir)) is None

    def test_clearing_leaves_nothing_behind(self, project_dir):
        wstate.observe(str(project_dir), blind=True, busy=False, arming=False)
        wstate.clear(str(project_dir))

        assert wstate.read(str(project_dir)) is None
        assert not (project_dir / ".tausik" / ".chat-watch.state.json").exists()


class TestARefusalThatIsStillHolding:
    """Плашка после отказа читалась как «жду тишины · 695 с», и человек понял
    её буквально: наблюдатель ждёт паузы, которая уже одиннадцать минут как
    наступила. Он отсиживал запрет, и на экране этого не было сказано."""

    def test_a_holding_refusal_is_not_waiting_for_silence(self):
        phase = wstate.phase_of(
            blind=False, busy=False, arming=False, percent=42, threshold=30, cooling=695.0
        )

        assert phase == wstate.PHASE_COOLING

    def test_it_says_how_long_is_left(self):
        said = wstate.phrase(wstate.PHASE_COOLING, cooling=695.4)

        assert "695" in said and "отменили" in said

    def test_a_spent_refusal_goes_back_to_waiting(self):
        """НЕГАТИВНЫЙ: остывание кончилось — состояние обычное, иначе плашка
        соврёт в другую сторону и уборка будет выглядеть запрещённой всегда."""
        phase = wstate.phase_of(
            blind=False, busy=False, arming=False, percent=42, threshold=30, cooling=0.0
        )

        assert phase == wstate.PHASE_WAITING

    def test_the_countdown_outranks_the_refusal(self):
        """Взведённая уборка важнее: она уже идёт, а запрет — то, что было."""
        phase = wstate.phase_of(
            blind=False, busy=False, arming=True, percent=42, threshold=30, cooling=600.0
        )

        assert phase == wstate.PHASE_ARMING



