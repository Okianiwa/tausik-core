"""QG-0: чем описание отсутствующего входа отличается от обещания «проблем не будет».

Правило негации вводилось против ложных срабатываний: «works without any
errors» содержит слово error, но сценария в нём нет. Оно вырезало фразу вместе
с шестьюдесятью символами после — и заодно съедало то, ради чего критерий
писался: «прогон без единой записи (журнал пуст)» переставал быть негативным
сценарием, потому что «пуст» попадал в вырезанный кусок.
"""

from gate_negative_scenario import has_negative_scenario


def test_a_missing_key_is_a_scenario():
    """Живой отказ: у задачи было три негативных критерия из семи, гейт не
    засчитал ни одного."""
    assert (
        has_negative_scenario(
            "4. записи журнала без ключей cache_write/output или со значением "
            "None не роняют расчёт"
        )
        is True
    )


def test_an_empty_journal_is_a_scenario():
    assert has_negative_scenario("5. прогон без единой записи (журнал пуст) даёт прочерк") is True


def test_the_russian_marker_counts_like_the_latin_one():
    """Сообщение гейта само предлагает писать по-русски."""
    assert has_negative_scenario("3. НЕГАТИВНЫЙ: значение вне диапазона игнорируется") is True


def test_a_promise_of_no_trouble_is_still_not_a_scenario():
    """То, ради чего правило негации и заводилось."""
    assert has_negative_scenario("Works without any errors") is False
    assert has_negative_scenario("1. Работает. 2. Без ошибок.") is False
    assert has_negative_scenario("2. Нет ошибок при штатной работе.") is False


def test_criteria_without_any_boundary_case_still_fail():
    """Иначе гейт перестал бы отказывать вообще — а это его работа."""
    assert has_negative_scenario("1. Работает корректно на валидном вводе.") is False


def test_the_root_covers_the_case_that_used_to_slip_through():
    """«ошибок» не начинается с «ошибк» — старый корень его не видел, и строка
    проходила только потому, что её целиком вырезала негация."""
    assert has_negative_scenario("Возвращает ошибок список при сбое") is True
