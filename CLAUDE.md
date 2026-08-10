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
- **Нет коммита без gates.** `pre-commit` → `gate_runner commit` по staged. Исправь blocking failures.
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
Session: none | Branch: fix/hook-tool-coverage | Version: 1.4.0
Tasks: 77/101 done, 0 active, 1 blocked
Blocked: asynchronus-ci-annotations-lookup

### Memory tail
Decisions (5):
- #68 Динамическую секцию CLAUDE.md собирает единственный билдер — service_claudemd.build_dynamic_content; CLI и MCP только де
- #67 Скоуп гейтов — свойство ВЛАДЕЛЬЦА файла: гейты проекта судят только его собственные файлы, чужие выносятся из скоупа, но
- #66 CoreBudget.SHARE остаётся 1.0 (все логические ядра): свип воркеров на 50k ОПРОВЕРГ гипотезу о вреде SMT — 1 поток на физ
- #65 mypy на commit-триггере не судит no-any-return: на неполном срезе этот код структурно недостоверен. Механизм — новый клю
- #64 Реестр гейтов — свойство ПРОЕКТА, а не места запуска кода: default_registry() резолвит builtin-стеки из каталога .claude
Conventions (5):
- #137 Excerpt реальных данных в тесте сверяют рендером по самим реальным данным
- #135 Гейт судит только файлы СВОЕГО проекта, а вынесенные называет вслух
- #131 Ложный блок гейта закрывают замером кодов ошибок, а не аннотацией точек
- #129 Совет ремедиации проверяют исполнением текста, а не чтением глазами
- #127 Вердикт гейта проверяют на РЕНДЕРЕ, а не на возвращаемой структуре
Dead ends (3):
- #136 Лечить чужие файлы в скоупе гейтов ОДНОЙ фильтрацией: выкинуть внешние пути из списка и оставить гей
- #132 Лечить ложный блок статического анализатора точечной аннотацией возврата в местах, где он сработал
- #101 Пивот consumer-pipeline: специализированный push (reusable ThreadLocal-предикат, inline pushableBy,
<!-- DYNAMIC:END -->
