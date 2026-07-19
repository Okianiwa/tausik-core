**English** | [Русский](../ru/hooks.md)

# Hooks (v1.4)

TAUSIK uses Claude Code hooks for automatic quality control. Hooks intercept agent actions **before** and **after** execution — they are gates, not instructions. **18 Python hooks** ship with v1.4 (1.3.7 had 16 + 1 shell = 17; v1.4 adds `secret_scan.py`, and the shell `pre-commit` is replaced by the Python `pre_commit_gates.py`, which runs the commit gates). Type checking did not go missing in that swap: mypy is now one of those gates — see "The mypy gate" below.

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
| `pre-commit` | Before `git commit` | (1) **Mojang artifact check** — `mojang_artifact_scan.py`, runs first: it is the only failure here that a follow-up commit cannot undo. (2) **Commit gates** via `gate_runner.py commit` (`ruff` + `mypy` + `filesize` + `bootstrap_drift`, all blocking). Implementation: `scripts/hooks/pre_commit_gates.py`. |

The Mojang check identifies artifacts **by content**, not by name: archives are opened and inspected for `net/minecraft/**` and `META-INF/versions/*/server-*.jar`, alongside forbidden paths (`async-platform/mc/{server,jre}/`) and loose extracted classes. So `mv minecraft_server.jar backup.jar` does not evade it, while the legitimate `gradle-wrapper.jar` (`org/gradle/**` inside) passes. `.gitignore` covers accidents but not `git add -f` — that flag exists precisely to stage ignored files — which is why the check sits on the commit.

The `bootstrap_drift` gate guards the second copy of the code. `scripts/` is the source — git tracks it, pytest imports it, the other commit gates judge it. The runtime (MCP server, hooks) executes the deploy under `.<ide>/scripts/` (usually `.claude/scripts/`), produced by `bootstrap.py` and kept out of git. Edit the source without re-bootstrapping and every signal goes green at once while the change never reaches the process that runs it: that is how an encoding fix survived a whole session without ever taking effect. `tausik doctor` sees this, but it is advisory and hand-run — the gate turns the rule into a mechanism. It judges everything git tracks under `scripts/` (not just `.py` — bootstrap copies the whole tree), compares with `CRLF→LF` normalisation, and **blocks rather than auto-deploying** (decision #55): a commit must not mutate state outside the index, and a running MCP server holds the old modules in memory regardless, so a silent redeploy would restore exactly the false-green it exists to kill.

Two rules keep the gate to what it actually understands.

**Only inside a TAUSIK source checkout.** "`scripts/` deploys to `.<ide>/scripts/`" is true of this repository, not of the projects TAUSIK bootstraps. A consumer with its own `scripts/etl_job.py` would otherwise be permanently un-committable — and told to run a `bootstrap/bootstrap.py` its checkout does not contain, leaving `--no-verify` as the only exit. The marker is positive: `bootstrap/bootstrap.py` **and** `harness/` under the project root; without both, the gate answers "Not a TAUSIK source checkout" and passes.

**Index drift and deploy drift need different remedies.** The gate judges the index; `bootstrap.py` deploys the worktree. When those disagree, re-bootstrapping copies the same worktree bytes again — the block survives its own remedy and the loop never terminates. So on a mismatch the gate also compares the deploy against the worktree: equal means the index is at fault and the report says `git add`; unequal means the deploy is at fault and the report says `python bootstrap/bootstrap.py`. An absent deploy is not a violation — nothing deployed means nothing can be stale. An unresolvable project root, by contrast, blocks: a gate that answers "the copies agree" because it could not find one of them is worse than no gate.

The file list does not travel in argv. Windows caps a command line at 32767 chars — measured here by binary search: 979 paths fit, 980 do not — and overflow surfaces as `FileNotFoundError: [WinError 206]`, a class name that points nowhere near the cause. So `git checkout-index` takes paths on stdin (`--stdin -z`) and `gate_runner` takes a NUL-terminated manifest (`--files-from`). NUL matches the `-z` form `staged_files` already reads; it is belt-and-braces rather than a live hazard — git's own `verify_path()` rejects a path holding a raw `\r` or `\n` (checked against git 2.49: `git update-index --cacheinfo` answers `error: Invalid path`), and Win32 will not create such a filename either. Batching was rejected (decision #56): several gates judge the file SET — `tdd_order` would fail a batch holding sources whose tests landed in another, and stack inference reads the union of extensions. That changes what the gates mean, not how they are invoked.

Gates judge the **staged** content, not the worktree: `git checkout-index` materializes the index into a temp tree that mirrors repo-relative paths (this matters — `filesize`'s `exempt_files` and `ruff`'s `per-file-ignores` are path-scoped). So "stage a clean version, then keep editing" yields neither a false pass nor a false block.

This is **not** "scoped quality gates" — those run via `tausik verify` (heavy stack: pytest/tsc/cargo/phpstan/…) and are decoupled from `git commit` since the v1.4 Verify-First Contract.

### The mypy gate

TAUSIK advertised type checking for a long time without running it anywhere. The legacy shell `pre-commit` declared `python -m mypy` blocking, but was never installed into `.git/hooks/` — and had it been, it would have rejected every commit, because mypy was not a project dependency. A CI step existed but ended in `|| true`, reporting green over 13 genuine errors. Three places claimed the check; none performed it.

The `mypy` gate is enabled, blocking, and runs on **two** triggers — `commit` and `task-done`. The pair is dictated by how the hook feeds its gates. On commit the index is judged: `git checkout-index` materializes **only staged** files into a temp tree. `ruff` copes because it works per file; mypy is cross-module, and every unchanged neighbour reads to it as a missing module. Hence `--ignore-missing-imports` in the command — without it a blocking gate would reject nearly any commit touching a module that imports a sibling, citing `import-not-found` in a file the author wrote correctly.

The cost of that flag is measured and bounded: errors **inside** the staged slice are caught, including types crossing between two files changed together; references into modules absent from the slice degrade to `Any` and go silently unchecked. The second trigger closes that blind spot — on `task-done` the same command runs against the real worktree, where the graph is complete and the verdict is whole. CI runs the same check.

The command deliberately takes **no** paths (there is no `{files}` in it): mypy reads its scope from `[tool.mypy]` in `pyproject.toml`. Explicit paths broke that three ways — they bypassed `exclude` (dragging in `scripts/hooks/`, excluded over dual-module-names), pulled in the import graph and reported errors in files the commit never touched, and silently dropped some per-module overrides with an `unused section(s)` note. `file_extensions: [".py"]` is kept and governs **relevance**: a docs-only commit skips the gate rather than running it.

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
