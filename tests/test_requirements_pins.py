"""Guard test: зависимости, чей API вызывается напрямую, обязаны иметь потолок мажора.

Живой отказ (2026-07-31). `requirements.txt` разрешал `mcp>=1.0.0` без верхней
границы. Вышел `mcp` 2.0.0 — ломающий мажор: декораторы `Server.list_tools` /
`call_tool` убраны, вместо `Server` появился `MCPServer`. Свежий bootstrap честно
поставил 2.0.0, и все три сервера из `harness/claude/mcp/` начали падать на старте
с `AttributeError` ещё до JSON-RPC цикла.

Дорого это стоило не самим падением, а тем, как оно выглядело: хост печатал только
`Failed to connect — Connection closed`, без трейсбека; CLI, хуки и `doctor`
оставались зелёными. Проект выглядел здоровым, агент молча уходил на CLI-фоллбек и
терял QG-0/QG-2 через MCP. Перезапуск сессии не помогал — процесс умирал одинаково
каждый раз, и это читалось как «MCP просто не подцепился».

Верхняя граница нужна ровно тем пакетам, чьи объекты мы импортируем и чьими
декораторами пользуемся: их мажор ломает нас молча. Транзитивные зависимости сюда
не входят — их пусть пинит тот, кто их вызывает.
"""

from __future__ import annotations

import os
import re

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_REQUIREMENTS = os.path.join(_ROOT, "requirements.txt")

# Пакеты, чей API вызывается напрямую из harness/. Мажор любого из них ломает
# сервер на старте, а не на краю — потолок обязателен.
DIRECT_API_DEPS = ("mcp",)

_UPPER_BOUND_RE = re.compile(r"[<~]=?\s*\d")


def _requirement_lines(text: str) -> list[str]:
    """Строки требований без комментариев и пустых."""
    lines = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            lines.append(line)
    return lines


def missing_upper_bound(text: str, package: str) -> bool:
    """True, если `package` объявлен, но без ограничения сверху.

    Отсутствие пакета в файле — тоже True: незакреплённая зависимость и
    исчезнувшая зависимость одинаково означают «потолок не гарантирован».
    """
    for line in _requirement_lines(text):
        name = re.split(r"[<>=!~\[;\s]", line, maxsplit=1)[0].strip().lower()
        if name == package.lower():
            return not _UPPER_BOUND_RE.search(line)
    return True


def test_direct_api_deps_are_capped():
    with open(_REQUIREMENTS, encoding="utf-8") as fh:
        text = fh.read()
    uncapped = [p for p in DIRECT_API_DEPS if missing_upper_bound(text, p)]
    assert not uncapped, (
        f"Нет потолка мажора у {uncapped} в requirements.txt. "
        "Ломающий мажор попадёт в venv при следующем bootstrap и уронит "
        "harness/claude/mcp/*/server.py на старте — молча, с одним "
        "'Connection closed' в хосте."
    )


def test_guard_catches_uncapped_pin():
    """Мутация: детектор обязан краснеть на входе, которым нас и сломало."""
    assert missing_upper_bound("mcp>=1.0.0", "mcp")
    assert missing_upper_bound("mcp", "mcp")
    assert missing_upper_bound("# mcp>=1.0.0,<2", "mcp"), "комментарий — не требование"
    assert missing_upper_bound("httpx>=0.27,<1", "mcp"), "чужой пин не считается"

    assert not missing_upper_bound("mcp>=1.0.0,<2", "mcp")
    assert not missing_upper_bound("mcp~=1.29", "mcp")
    assert not missing_upper_bound("MCP >= 1.0.0, < 2.0.0", "mcp")
