"""fix-relevant-files-nargs: JSON-строка как единственный аргумент --relevant-files отвергается
громко (не молча пишет двойную кодировку в БД); пути через пробел проходят."""

from __future__ import annotations

import os
import sys

import pytest

_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from project_cli_task import _reject_json_string_arg  # noqa: E402
from tausik_utils import ServiceError  # noqa: E402


def test_json_string_arg_rejected():
    """AC#1 негатив: единственный аргумент-JSON → ServiceError с правильным вызовом."""
    with pytest.raises(ServiceError) as ei:
        _reject_json_string_arg(['["a/b.py", "tests/test_b.py"]'])
    msg = str(ei.value)
    assert "JSON" in msg
    assert "a/b.py" in msg  # правильный вызов подсказан в сообщении


def test_quoted_string_arg_rejected():
    """Граница: аргумент начинается с кавычки (JSON-фрагмент) → тоже отвергнут."""
    with pytest.raises(ServiceError):
        _reject_json_string_arg(['"a/b.py"'])


def test_space_separated_paths_pass():
    """AC#1 позитив: пути через пробел (nargs='*' даёт список) → проходят как есть."""
    vals = ["a/b.py", "tests/test_b.py"]
    assert _reject_json_string_arg(vals) == vals


def test_single_plain_path_passes():
    """Один обычный путь (не JSON, не кавычка) → проходит."""
    assert _reject_json_string_arg(["src/foo.py"]) == ["src/foo.py"]


def test_none_and_empty_pass():
    """None/пустой список — гейт неприменим (nargs='*' default None)."""
    assert _reject_json_string_arg(None) is None
    assert _reject_json_string_arg([]) == []


def test_mutation_guard_json_bracket_is_the_trigger(monkeypatch):
    """Convention #22: намеренная мутация — если снять [-детект, JSON-строка пройдёт (регресс).
    Проверяем, что именно ведущий '[' триггерит отказ (реальный класс бага), а не что-то иное."""
    # Ведущий '[' → отказ (реальный кейс двойной кодировки).
    with pytest.raises(ServiceError):
        _reject_json_string_arg(['["x.py"]'])
    # Тот же контент без ведущего '[' (обычный путь) → НЕ отказ (граница детектора точна).
    assert _reject_json_string_arg(["x.py"]) == ["x.py"]
