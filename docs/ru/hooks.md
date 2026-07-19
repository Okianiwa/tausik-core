[English](../en/hooks.md) | **Русский**

# Хуки (v1.4)

TAUSIK использует хуки Claude Code для автоматического контроля качества. Хуки перехватывают действия агента **до** и **после** выполнения — это шлюзы, не инструкции. **18 Python-хуков** идут с v1.4 (1.3.7 имел 16 + 1 shell = 17; v1.4 добавляет `secret_scan.py`, а shell-`pre-commit` заменён Python-реализацией `pre_commit_gates.py`, которая запускает commit-гейты вместо mypy).

## Что такое хуки

Хуки — скрипты, запускающиеся автоматически на каждое действие агента. Они решают, можно ли действие выполнять (PreToolUse), что делать после (PostToolUse) или что записать на границах сессии/агента (SessionStart, Stop, UserPromptSubmit). Общие хелперы живут в `scripts/hooks/_common.py` (сам по себе не хук); regex-набор `scripts/hooks/memory_markers.py` — библиотека, импортируемая `memory_posttool_audit.py` и pipeline'ом скраббинга brain.

## PreToolUse — шлюзы перед действием

| Хук | Когда | Что делает |
|------|-------|-----------|
| `task_gate.py` | Перед Write/Edit | Блокирует изменения файлов, если нет активной задачи (SENAR Rule 9.1) |
| `bash_firewall.py` | Перед Bash | Блокирует опасные команды (rm -rf, DROP TABLE, force push, и т.д.) |
| `git_push_gate.py` | Перед git push | Блокирует push без свежего, одноразового тикета `.tausik/.push_ticket.json`, привязанного к SHA HEAD. `/ship` и `/commit` запускают `tausik push-ok && git push` после вашего "y" — `push-ok` пишет 60-секундный тикет, хук съедает его на следующем push. |
| `memory_pretool_block.py` | Перед Write в auto-memory | Блокирует cross-project записи без `confirm: cross-project` в промпте |
| `secret_scan.py` (v1.4) | Перед Write/Edit/MultiEdit | Сканирует `tool_input` на типичные секреты (AWS/GitHub/Slack/Stripe/OpenAI/Anthropic токены, JWT, блоки приватного ключа, generic `password`/`api_key`). По умолчанию warning; `TAUSIK_SECRET_SCAN_STRICT=1` — блокировка. (SENAR Rule 10.12) |

## PostToolUse — реакции после действия

| Хук | Когда | Что делает |
|------|-------|-----------|
| `auto_format.py` | После Write/Edit | Авто-форматирование через ruff/prettier/gofmt + лог "Modified: X" в задачу |
| `task_call_counter.py` | После любого tool call | Инкрементирует per-task `call_actual` счётчик; warning'ит на 1.5×budget |
| `activity_event.py` | После любого tool call | Записывает activity-таймстемпы для **gap-based active-time** метрики (SENAR Rule 9.2) |
| `memory_posttool_audit.py` | После Write в auto-memory | Аудитит cross-project leakage (использует regex-библиотеку `memory_markers.py`) и предупреждает |
| `brain_post_webfetch.py` | После WebFetch | Авто-кешит результат в shared brain `web_cache` для token reuse |
| `task_done_verify.py` | После `task_done` / `task_done` | Аудитит AC evidence через 5 правило-base проверок (Ralph-mode-lite). Matcher v1.4: `tausik_task_done\|tausik_task_done\|Bash` |

## SessionStart / SessionEnd

| Хук | Когда | Что делает |
|------|-------|-----------|
| `session_start.py` | На старте сессии | Авто-инжектит status + Memory Block — без ручного `/start` |
| `session_metrics.py` | На завершении сессии | Записывает session metrics (active vs wall, throughput) в БД |
| `session_cleanup_check.py` | На остановке агента | Предупреждает об открытом exploration / review-задачах / session timeout |

## UserPromptSubmit / Stop

| Хук | Когда | Что делает |
|------|-------|-----------|
| `user_prompt_submit.py` | На пользовательском промпте | Распознаёт coding-intent (EN+RU) → подталкивает, если нет активной задачи |
| `keyword_detector.py` | На остановке агента | Ловит "I'll implement"/"сейчас напишу" drift-фразы → блокирует stop |
| `brain_search_proactive.py` | Перед WebSearch/WebFetch | Проактивно query'ит shared brain на релевантные decisions/patterns перед web-вызовами |

## Git pre-commit

| Хук | Когда | Что делает |
|------|-------|-----------|
| `pre-commit` | Перед `git commit` | (1) **Проверка артефактов Mojang** — `mojang_artifact_scan.py`, идёт первой: это единственный отказ, который нельзя исправить следующим коммитом. (2) **Commit-гейты** через `gate_runner.py commit` (`ruff` + `filesize` + `bootstrap_drift`, все blocking). Реализация: `scripts/hooks/pre_commit_gates.py`. |

Проверка Mojang опознаёт артефакт **по содержимому**, а не по имени: jar вскрывается и проверяется на `net/minecraft/**` и `META-INF/versions/*/server-*.jar`, плюс запретные пути (`async-platform/mc/{server,jre}/`) и распакованные классы. Поэтому `mv minecraft_server.jar backup.jar` не обходит проверку, а законный `gradle-wrapper.jar` (внутри `org/gradle/**`) проходит. `.gitignore` закрывает случайность, но не `git add -f` — флаг существует ровно для того, чтобы добавить игнорируемое; поэтому проверка стоит на коммите.

Гейт `bootstrap_drift` сторожит вторую копию кода. Источник — `scripts/`: он в git, его импортирует pytest, его же судят остальные commit-гейты. Исполняет рантайм (MCP-сервер, хуки) деплой в `.<ide>/scripts/` (обычно `.claude/scripts/`), который делает `bootstrap.py` и который вне git. Правка источника без ре-bootstrap'а красит зелёным все сигналы разом, ни разу не дойдя до процесса, который её исполняет: так фикс кодировок прожил целую сессию, не сработав ни раз. `tausik doctor` это видит, но он advisory и запускается руками — гейт превращает правило в механику. Судит всё, что git отслеживает под `scripts/` (не только `.py` — bootstrap копирует дерево целиком), сравнивает с нормализацией `CRLF→LF`, и **блокирует, а не автодеплоит** (решение #55): коммит не должен менять состояние вне индекса, а уже запущенный MCP-сервер всё равно держит старые модули в памяти — тихий редеплой вернул бы ровно ту ложную зелень, против которой гейт и заведён.

Два правила ограничивают гейт тем, что он действительно понимает.

**Только внутри исходного чекаута TAUSIK.** Инвариант «`scripts/` разворачивается в `.<ide>/scripts/`» верен для этого репозитория, а не для проектов, которые TAUSIK бутстрапит. Потребитель со своим `scripts/etl_job.py` иначе стал бы навсегда незакоммичиваемым — и получил бы совет запустить `bootstrap/bootstrap.py`, которого в его чекауте нет, оставив `--no-verify` единственным выходом. Признак позитивный: `bootstrap/bootstrap.py` **и** `harness/` под корнем проекта; без них гейт отвечает «Not a TAUSIK source checkout» и пропускает.

**Дрейф индекса и дрейф деплоя лечатся разным.** Гейт судит индекс, а `bootstrap.py` разворачивает рабочую копию. Когда они расходятся, повторный bootstrap копирует те же байты worktree — блокировка переживает собственную ремедиацию, и цикл не завершается. Поэтому при расхождении гейт дополнительно сверяет деплой с рабочей копией: совпали — виноват индекс, и в отчёте стоит `git add`; не совпали — виноват деплой, и в отчёте `python bootstrap/bootstrap.py`. Отсутствующий деплой нарушением не считается: не развёрнуто — значит нечему устареть. Нерезолвящийся корень проекта, наоборот, блокирует: гейт, ответивший «копии совпадают» потому, что не нашёл одну из них, хуже отсутствующего.

Гейты судят **staged-содержимое**, а не рабочую копию: `git checkout-index` разворачивает индекс во временное дерево с сохранением относительных путей (это важно — `exempt_files` у `filesize` и `per-file-ignores` у `ruff` привязаны к путям). Поэтому «добавил чистую версию, потом продолжил править» не даёт ни ложного пропуска, ни ложной блокировки.

Это **не** «scoped quality gates» — те запускаются через `tausik verify` (тяжёлый стек: pytest/tsc/cargo/phpstan/…) и развязаны с `git commit` начиная с v1.4 Verify-First Contract.

### Установка

Ставится **автоматически** при `bootstrap.py` (`bootstrap/bootstrap_git_hooks.py`) — руками копировать ничего не нужно. В `.git/hooks/pre-commit` кладётся тонкий sh-шим, который на каждом запуске резолвит актуальную реализацию из `scripts/hooks/`, поэтому правка исходника действует сразу, без переустановки. Чужой (не-TAUSIK) `pre-commit` никогда не перезаписывается — bootstrap оставит его и предупредит.

Переустановить вручную:

```bash
python -c "import sys; sys.path.insert(0,'bootstrap'); from bootstrap_git_hooks import install_git_hooks; print(install_git_hooks('.'))"
```

> **Не используйте `git config core.hooksPath scripts/hooks`.** Раньше это был рекомендованный способ; теперь он **отключит** установленный хук — git станет искать хуки только в указанной папке. Установка идёт через bootstrap в `.git/hooks/`.

> **Windows.** Шим — POSIX sh (git на Windows запускает хуки через свой bundled sh), сама логика на Python. Python берётся из venv проекта, с откатом на системный. Отдельная `.cmd`-обёртка не нужна.

### Отключение / bypass

- Разово: `git commit --no-verify`.
- Для сессии/CI: `TAUSIK_SKIP_COMMIT_GATES=1`.
- Снять совсем: `bootstrap_git_hooks.uninstall_git_hooks('.')` (удаляет только TAUSIK-хуки, чужие не трогает).

## Как это работает

```
Вы: "добавь кнопку на главную"

Агент хочет редактировать index.html
  → task_gate.py проверяет: есть ли активная задача? Нет → ЗАБЛОКИРОВАНО
  → Агент создаёт задачу через /plan, стартует
  → task_gate.py проверяет снова: задача есть → РАЗРЕШЕНО

Агент редактирует index.html
  → auto_format.py: форматирует prettier'ом
  → auto_format.py: пишет "Modified: index.html" в задачу
  → task_call_counter.py: бампит call_actual; warning на 1.5×budget
  → activity_event.py: штампует activity-таймстемп (active-time)

Агент: tausik task done my-button --ac-verified
  → task_done_verify.py: 5-проверочный AC-аудит
```

## Коды возврата

| Код | Значение | Поведение |
|------|---------|----------|
| 0 | Успех | Действие разрешено |
| 1 | Warning | Действие разрешено; warning записан |
| 2 | Block | Действие **отменено**; агент получает причину |

## Что блокирует `bash_firewall`

- `rm -rf /` и `rm -rf .` — удаление файловой системы
- `DROP TABLE`, `DROP DATABASE`, `TRUNCATE TABLE` — удаление данных
- `git reset --hard` — потеря локальных изменений
- `git push --force` — перезапись remote-истории
- `git clean -fd` — удаление untracked-файлов
- `dd if=/dev/zero`, `mkfs.` — форматирование диска
- Fork bombs

## Отключение хуков

Для тестирования или дебага: установите `TAUSIK_SKIP_HOOKS=1`.

В `.claude/settings.json` хуки генерируются автоматически на bootstrap. Чтобы отключить конкретный хук, удалите его из секции `hooks`. Для регенерации файла запустите `python .tausik-lib/bootstrap/bootstrap.py --refresh`.

## См. также

- **[Workflow](workflow.md)** — как хуки вписываются в рабочий цикл
- **[Session Active Time](session-active-time.md)** — что питает `activity_event.py`
- **[CLI команды](cli.md)** — управление задачами из терминала
