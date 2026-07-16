# CLAUDE.md

Инструкции для AI-агента в этом репозитории. Следуй им строго.

# TAUSIK — фреймворк AI-агентов

Реализует [SENAR v1.3](https://senar.tech). Задачи, сессии, качество, проектная память.

Stack: Python 3.11+ stdlib | CLI `.tausik/tausik` | DB SQLite+FTS5 | Tests pytest. Все данные в `.tausik/` (единственный .gitignore).

## Принципы

- **Нулевая толерантность к тихим ошибкам.** Ошибка CLI — заведи баг-задачу.
- **Agent-first.** Перед закрытием: "поймёт ли свежий агент?"
- **Dogfooding.** Этот фреймворк — наш же пользователь. Неудобно — баг.
- **SENAR.** Контекст важнее кода. Верификация важнее скорости. Знания важнее опыта.

## Ограничения (жёсткие)

- **Нет кода без задачи.** `task start <slug>` перед Write/Edit.
- **QG-0 Context Gate.** `task start` требует goal + acceptance_criteria.
- **QG-2 Verify-First.** Heavy gates через `tausik verify --task <slug>` (cache 10 мин), затем `task done --ac-verified` читает кэш. Edge-cases — `docs/ru/agent-contract.md`.
- **Нет коммита без gates.** Исправь blocking failures.
- **Нет прямого доступа к БД.** Только MCP/CLI.
- **Не угадывай аргументы CLI.** `tausik <cmd> --help` или `docs/ru/cli.md`.
- **Исходники в корне** (`scripts/`, `docs/`, `harness/`, `bootstrap/`). Не редактируй `.claude/` напрямую.
- **MCP-first.** MCP > CLI когда equivalent.
- **Git: спроси перед commit/push.**
- **Макс. 400 строк/файл.** Filesize gate. Исключения: тесты, generated.
- **Непрерывное журналирование.** `task log <slug> "msg"` после каждого шага.
- **Документируй dead ends.** `tausik dead-end "approach" "reason"`.
- **Checkpoint каждые 30-50 tool calls.** `/checkpoint`, `/end`.
- **Лимит сессии 180 мин ACTIVE** (gap-based ≥10мин = AFK).
- **Знания фреймворка остаются здесь.** Не сохраняй инструкции TAUSIK в auto-memory.

## Память

| Система | Когда |
|---|---|
| **TAUSIK memory** (`memory add`, `.tausik/tausik.db`) | Паттерны/dead ends/conventions ЭТОГО проекта |
| **Claude auto-memory** (`~/.claude/`) | Кросс-проектные привычки пользователя |

Типы: `pattern`, `gotcha`, `convention`, `context`, `dead_end`.
CLI: ВСЕГДА `.tausik/tausik <команда>`. НИКОГДА `python scripts/project.py` напрямую.

## Команды

```bash
.tausik/tausik status                          # обзор + предупреждения SENAR
.tausik/tausik task start <slug>               # активировать (QG-0)
.tausik/tausik verify --task <slug>            # heavy gates, cache 10 мин
.tausik/tausik task done <slug> --ac-verified  # завершить (QG-2)
.tausik/tausik task log <slug> "message"       # журнал
.tausik/tausik dead-end "approach" "reason"    # dead end
.tausik/tausik metrics                         # SENAR метрики + LLM cost
.tausik/tausik search "<query>"                # FTS5 поиск
.tausik/tausik doctor                          # health check
```

Статусы: `planning → active → blocked|review → done`.

## Reference

Полный контракт (estimation, SENAR matrix, roles, custom_stacks, QG-2): `docs/ru/agent-contract.md`. CLI: `docs/ru/cli.md`. Архитектура: `docs/ru/architecture.md`. Quickstart: `docs/ru/quickstart.md`. Changelog: `CHANGELOG.md`.

<!-- DYNAMIC:START -->
## Current State
Session: #9 (active) | Branch: feat/core-rewrite-blockentity-slice | Version: 1.4.0
Tasks: 16/23 done, 1 active, 0 blocked
Active: mc-tick-profile

### Memory tail
Decisions (5):
- #21 ГЛАВНАЯ УГРОЗА ЭПИКУ — ставка D (ниша), а не A. Самая большая НАЙДЕННАЯ реальная постройка (мега-хранилище SciCraft-клас
- #20 ДЕФЕКТ СОБСТВЕННОЙ ПРЕДРЕГИСТРАЦИИ, вскрытый постфактум: ставка A задана В ДОЛЯХ тика и потому допускала 'победу', ничег
- #19 ПРОТОТИП ЛЕГЧЕ РЕАЛЬНОСТИ, а не тяжелее: наш work=0 = 0.0108 мкс/шт, work=64 = 0.084, ВАНИЛЬ = 0.21 мкс. Реальная логика
- #18 Сцена прототипа (150k блок-энтити / 50k хопперов) ВЫЖИЛА проверку реальностью: порог лага ванили замерен на ~50-60k акти
- #17 Ставка A эпика ОТВЕЧЕНА и ответ СЦЕНОЗАВИСИМ: блок-энтити 1.65% тика в обычном выживаче (мобы 54% + чанки 40% = 94%) и 6
Conventions (2):
- #49 Пороги предрегистрации задавать в АБСОЛЮТЕ, а не в долях — доля бессмысленна, когда абсолют ничтожен
- #22 Тест, прошедший с первого раза, проверяется НАМЕРЕННОЙ мутацией — иначе он не доказательство
Dead ends (3):
- #34 Считать .gitignore механической защитой от коммита кода Mojang — «git add -f не пройдёт»
- #33 Folia НЕ держит совместимость плагинов — аргумент «свобода от Bukkit API» ложен
- #29 Фильтровать «живых детей» по `t.status!='done' AND t.archived_at IS NULL` — защита от того, что архи
<!-- DYNAMIC:END -->
