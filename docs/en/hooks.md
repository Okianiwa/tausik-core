**English** | [Русский](../ru/hooks.md)

# Hooks (v1.4)

TAUSIK uses Claude Code hooks for automatic quality control. Hooks intercept agent actions **before** and **after** execution — they are gates, not instructions. **18 Python hooks** ship with v1.4 (1.3.7 had 16 + 1 shell = 17; v1.4 adds `secret_scan.py`, and the shell `pre-commit` is replaced by the Python `pre_commit_gates.py`, which runs the commit gates instead of mypy).

## What Are Hooks

Hooks are scripts that run automatically with every agent action. They decide whether an action can be performed (PreToolUse), what to do afterward (PostToolUse), or what to record on session/agent boundaries (SessionStart, Stop, UserPromptSubmit). Shared helpers live in `scripts/hooks/_common.py` (not a hook itself); the regex set in `scripts/hooks/memory_markers.py` is a library imported by `memory_posttool_audit.py` and the brain-scrubbing pipeline.

## PreToolUse — Gates That Run Before an Action

| Hook | When | What It Does |
|------|------|-------------|
| `task_gate.py` | Before Write/Edit | Blocks file changes if no active task (SENAR Rule 9.1) |
| `bash_firewall.py` | Before Bash | Blocks dangerous commands (rm -rf, DROP TABLE, force push, etc.) |
| `git_push_gate.py` | Before git push | Blocks unless `.tausik/.push_ticket.json` is fresh, single-use, and bound to HEAD SHA. `/ship` and `/commit` run `tausik push-ok && git push` after your "y" — `push-ok` writes the 60-second ticket; the hook consumes it on the next push. |
| `memory_pretool_block.py` | Before Write to auto-memory | Blocks cross-project writes unless prompt contains `confirm: cross-project` |
| `secret_scan.py` (v1.4) | Before Write/Edit/MultiEdit | Scans `tool_input` for likely secrets (AWS/GitHub/Slack/Stripe/OpenAI/Anthropic tokens, JWT, private-key blocks, generic `password`/`api_key` literals). Warns by default; set `TAUSIK_SECRET_SCAN_STRICT=1` to block. (SENAR Rule 10.12) |

## PostToolUse — Reactions After an Action

| Hook | When | What It Does |
|------|------|-------------|
| `auto_format.py` | After Write/Edit | Auto-formats with ruff/prettier/gofmt + logs "Modified: X" to task |
| `task_call_counter.py` | After any tool call | Increments per-task `call_actual` counter; warns at 1.5×budget |
| `activity_event.py` | After any tool call | Records activity timestamps for **gap-based active-time** session metric (SENAR Rule 9.2) |
| `memory_posttool_audit.py` | After Write to auto-memory | Audits cross-project leakage (uses `memory_markers.py` regex library) and warns |
| `brain_post_webfetch.py` | After WebFetch | Auto-caches result in shared brain `web_cache` for token reuse |
| `task_done_verify.py` | After `task_done` / `task_done` | Audits AC evidence via 5 rule-based checks (Ralph-mode-lite). Matcher v1.4: `tausik_task_done\|tausik_task_done\|Bash` |

## SessionStart / SessionEnd

| Hook | When | What It Does |
|------|------|-------------|
| `session_start.py` | On session start | Auto-injects status + Memory Block — no manual `/start` needed |
| `session_metrics.py` | On session end | Records session metrics (active vs wall, throughput) to DB |
| `session_cleanup_check.py` | On agent stop | Warns about open exploration / review tasks / session timeout |

## UserPromptSubmit / Stop

| Hook | When | What It Does |
|------|------|-------------|
| `user_prompt_submit.py` | On user prompt | Detects coding-intent (EN+RU) → nudges if no active task |
| `keyword_detector.py` | On agent stop | Catches "I'll implement"/"сейчас напишу" drift phrases → blocks stop |
| `brain_search_proactive.py` | Before WebSearch/WebFetch | Proactively queries shared brain for relevant decisions/patterns before web calls |

## Git pre-commit

| Hook | When | What It Does |
|------|------|-------------|
| `pre-commit` | Before `git commit` | (1) **Mojang artifact check** — `mojang_artifact_scan.py`, runs first: it is the only failure here that a follow-up commit cannot undo. (2) **Commit gates** via `gate_runner.py commit` (`ruff` + `filesize`, both blocking). Implementation: `scripts/hooks/pre_commit_gates.py`. |

The Mojang check identifies artifacts **by content**, not by name: archives are opened and inspected for `net/minecraft/**` and `META-INF/versions/*/server-*.jar`, alongside forbidden paths (`async-platform/mc/{server,jre}/`) and loose extracted classes. So `mv minecraft_server.jar backup.jar` does not evade it, while the legitimate `gradle-wrapper.jar` (`org/gradle/**` inside) passes. `.gitignore` covers accidents but not `git add -f` — that flag exists precisely to stage ignored files — which is why the check sits on the commit.

Gates judge the **staged** content, not the worktree: `git checkout-index` materializes the index into a temp tree that mirrors repo-relative paths (this matters — `filesize`'s `exempt_files` and `ruff`'s `per-file-ignores` are path-scoped). So "stage a clean version, then keep editing" yields neither a false pass nor a false block.

This is **not** "scoped quality gates" — those run via `tausik verify` (heavy stack: pytest/tsc/cargo/phpstan/…) and are decoupled from `git commit` since the v1.4 Verify-First Contract.

### Install

Installed **automatically** by `bootstrap.py` (`bootstrap/bootstrap_git_hooks.py`) — nothing to copy by hand. `.git/hooks/pre-commit` gets a thin sh shim that resolves the live implementation under `scripts/hooks/` on every run, so editing the source takes effect immediately. A foreign (non-TAUSIK) `pre-commit` is never overwritten — bootstrap leaves it and warns.

Reinstall manually:

```bash
python -c "import sys; sys.path.insert(0,'bootstrap'); from bootstrap_git_hooks import install_git_hooks; print(install_git_hooks('.'))"
```

> **Do not use `git config core.hooksPath scripts/hooks`.** It used to be the recommended route; it now **disables** the installed hook, since git would look for hooks only in that directory. Installation goes through bootstrap into `.git/hooks/`.

> **Windows.** The shim is POSIX sh (git on Windows runs hooks through its bundled sh) and the logic is Python. Python is taken from the project venv, falling back to the system one. No separate `.cmd` wrapper needed.

### Disable / bypass

- One-off: `git commit --no-verify`.
- Session/CI: `TAUSIK_SKIP_COMMIT_GATES=1`.
- Remove entirely: `bootstrap_git_hooks.uninstall_git_hooks('.')` (removes TAUSIK hooks only, leaves foreign ones).

## How It Works

```
You: "add a button to the homepage"

Agent wants to edit index.html
  → task_gate.py checks: is there an active task? No → BLOCKED
  → Agent creates a task via /plan, starts
  → task_gate.py checks again: task exists → ALLOWED

Agent edits index.html
  → auto_format.py: formats with prettier
  → auto_format.py: logs "Modified: index.html" to the task
  → task_call_counter.py: bumps call_actual; warns at 1.5×budget
  → activity_event.py: stamps activity timestamp (active-time)

Agent: tausik task done my-button --ac-verified
  → task_done_verify.py: 5-check AC audit
```

## Exit Codes

| Code | Meaning | Behavior |
|------|---------|----------|
| 0 | Success | Action allowed |
| 1 | Warning | Action allowed; warning logged |
| 2 | Block | Action **cancelled**; agent receives the reason |

## What `bash_firewall` Blocks

- `rm -rf /` and `rm -rf .` — filesystem deletion
- `DROP TABLE`, `DROP DATABASE`, `TRUNCATE TABLE` — data deletion
- `git reset --hard` — loss of local changes
- `git push --force` — overwriting remote history
- `git clean -fd` — deleting untracked files
- `dd if=/dev/zero`, `mkfs.` — disk formatting
- Fork bombs

## Disabling Hooks

For testing or debugging: set `TAUSIK_SKIP_HOOKS=1`.

In `.claude/settings.json` hooks are generated automatically during bootstrap. To disable a specific hook, remove it from the `hooks` section. To re-generate the file, run `python .tausik-lib/bootstrap/bootstrap.py --refresh`.

## What's Next

- **[Workflow](workflow.md)** — how hooks fit the work cycle
- **[Session Active Time](session-active-time.md)** — what `activity_event.py` powers
- **[CLI Commands](cli.md)** — managing tasks from the terminal
