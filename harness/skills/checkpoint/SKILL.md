---
name: checkpoint
description: "Checkpoint session state to disk before compaction."
effort: fast
context: inline
---

<!-- Шапка здесь КОРОЧЕ, чем у той же страницы в личном комплекте, и это
     намеренно. Контракт TAUSIK держит `description` в кавычках и не длиннее
     60 знаков (`tests/test_skill_descriptions_length.py`), а хост выбирает
     скилл по описанию и любит подробное. Разошлись ровно шапки: тело —
     один текст на все копии, и расходиться ему нельзя. Проверено 27.08.2026:
     три копии (эта, `~/.claude/skills/`, `workspace-setup/skills/`)
     совпадают ниже фронтматтера байт в байт. -->


# /checkpoint — state onto disk while the conversation is still whole

Compaction folds the conversation into a summary. What survives it: the root
`CLAUDE.md`, unconditional rules, auto memory, the system prompt — and everything
written to disk. What does not: nested `CLAUDE.md` files, rules with `paths:`,
Serena's memory map, the contents of files that were read — and everything
worked out in the conversation but written down nowhere.

A checkpoint moves what is needed onto disk **before** the conversation folds.

The skill assumes neither a particular project nor a directory layout: where to
write is worked out in step 1, not taken from habit. TAUSIK steps (2b, 6) run
only where there is a `.tausik/` — elsewhere they are skipped without comment.

## Who asks for a checkpoint

Three callers, and the work is the same for all of them.

- **You yourself**, before a `/compact` you are about to run, and every 30–50
  tool calls (SENAR Rule 9.3).
- **The human**, typing `/checkpoint`.
- **The supervisor**, through the service: `tz checkpoint <who>`. It arrives as
  a plain `/checkpoint` message. Do not answer it with words — do the work: the
  service watches the file on disk and reports to the supervisor the moment it
  appears. A reply without a written file reads as "done" and is a lie.

## Reading the plan: pointwise, never whole

**Never read the working record whole.** Plans grow to a hundred thousand
tokens, and everything that lands in context is then paid for on every remaining
turn.

| what you need | how |
|---|---|
| where the work stopped | `find_symbol("Где я сейчас", relative_path=<plan>, include_body=true)` |
| the map of the record | `get_symbols_overview(<plan>, depth=2)` |
| one particular section | `find_symbol("<heading>", relative_path=<plan>, include_body=true)` |

Measured 22.08 on the ecosystem's largest plan: the whole file is **98 600**
tokens, its «Где я сейчас» is **1 100**, its map is **2 400**. Markdown headings
are symbols to Serena, with boundaries — `replace_symbol_body` rewrites a whole
section without counting lines, and that is also how you edit the record in
step 2.

## Step 1 — where to write

Three cases, in descending order of frequency.

**A working record already exists.** The file where this task is described: a
plan, a note, a checklist, an issue in the repository. The quick way not to
start a second such file is to look at the previous pointer — it names the
record outright:

```bash
cat .claude/.checkpoint-* 2>/dev/null
```

**There is no record and the task is multi-step.** Start the file yourself — in
one action, without ceremony. Choose the place by what is already customary in
this repository (`docs/plans/`, `docs/`, `notes/` — look at what is there), not
by a general rule. The repository is somebody else's or there is none — write
into the session's scratchpad directory. Contents: what we are after, what is
already done, what comes next. This is not a plan to be approved but a record
for whoever continues.

**The task is one-off, no file needed.** Say so and write the state as a single
paragraph into the `next` field of the pointer from step 5. Do not start
separate files for this: the pointer is the only name the readers know.

## Step 2 — bring the record in line with reality

- Mark what is done (`- [x]`, if the record has marks at all).
- Add what went off plan — in your own words, in whatever form the record
  keeps it.
- Add this session's dead ends: what was tried and why it was dropped. **This
  is the most expensive thing to recover.** The current state is derivable from
  the code, the history of discarded approaches is derivable from nothing.

The number of marks only grows. It went down — stop and say so: somebody else's
work has been overwritten.

⚠ Edit **the whole block, up to the boundary of the next heading**, not "from
this spot to the end of the file": a tail cut once already erased 291 lines of
the chronicle. `replace_symbol_body` on the heading does exactly this and cannot
overshoot.

## Step 2b — the project's own record (only where there is a `.tausik/`)

The plan is the human's record; the TAUSIK database is the machine's, and the
gates read it. A dead end written only into the plan is invisible to
`tausik metrics`, and a step closed only in the database is invisible to whoever
opens the plan. Both, or the two sources drift.

In parallel, MCP tools preferred over the CLI:

- `tausik_session_current` — how long this session has run;
- `tausik_task_list` with `status=active` — what is open;
- `tausik_status` — warnings.

Dead ends found in step 2 go in with `tausik_dead_end`, progress with
`tausik_task_log`. Do not re-inject `memory_block`: it was loaded on `/start` and
lives in the context; repeating it costs about 600 tokens per checkpoint and adds
nothing. A targeted lookup is `tausik_memory_search`.

**SENAR Rule 9.2.** `tausik_status` shows a session-duration warning — say it to
the owner prominently, and say it at every checkpoint while it stands:

> Сессия идёт X мин (предел Y). Пора закрываться через `/end`.

## Step 3 — write «Где я сейчас»

At the end of the record, replacing the previous such block:

```markdown
## Где я сейчас  (обновлено ГГГГ-ММ-ДД ЧЧ:ММ)

**Фаза:** <какая, какой шаг в работе>
**В работе прямо сейчас:** <что редактируется, что не закончено>
**Следующее действие:** <одна конкретная фраза — с чего продолжить>
**Незакоммичено:** <файлы, или «чисто»>
**Открытые вопросы:** <что ждёт ответа владельца, или «нет»>
**Фоновое:** <запущенные агенты, процессы, или «нет»>
```

«Следующее действие» is written so that work can start from it without reading
the rest. Not «продолжить рефакторинг», but «дописать `replaceWorker` в
`internal/scan/pool.go`, тест уже падает на `TestPoolDrain`».

This block is what the next session reads *instead of* the plan. Keep it
self-sufficient and short: it is read on every recovery, and everything in it is
paid for on every turn afterwards.

## Step 4 — the TAUSIK handoff (only where there is a `.tausik/`)

`tausik_session_handoff` with the same facts as «Где я сейчас», in the shape the
next session's `/start` expects:

```json
{
  "completed": ["task-slug-1: brief description"],
  "in_progress": [{"slug": "task-slug-2", "state": "step 3 of 5"}],
  "key_files": ["scripts/file1.py"],
  "dead_ends": [],
  "next_steps": ["Continue task-slug-2"],
  "warnings": ["Note for next checkpoint"]
}
```

Then `tausik_update_claudemd` to refresh the project's dynamic section.

⚠ The handoff JSON must be valid JSON — an unescaped quote inside a value breaks
the call, and it breaks it silently enough that the checkpoint looks written.

## Step 5 — write the pointer

**This step is the one the guards actually read. Skipping it means the
checkpoint did not happen, whatever else was written.**

```bash
state --self          # prints the id and name of the current session
```

Put the fields into a file **outside the project** — the session scratchpad —
and feed it to the hook:

```json
{
  "session_id": "556a4872-…",
  "plan": "docs/plans/2026-08-11-scan-pool.md",
  "project": "<the Serena project to activate>",
  "next": "<the same sentence as «Следующее действие»>"
}
```

```bash
python ~/.claude/hooks/checkpoint.py save < <that file>   # python3 on Linux
```

The hook prints the path it wrote — that line is the receipt. `plan` — the path
to the working record (empty if there is none), `project` — what to activate in
Serena, `next` — where to continue from. The working directory and the timestamp
it fills in itself; an empty `next` it refuses, because such a pointer passes the
guard and still tells the next session nothing.

**Do not write the file by hand.** It lies inside the project, and TAUSIK's gates
(`task_gate`, `bash_write_gate`) tell apart only «inside the tree / outside»:
with the last task closed, Rule 1 refused the write, and the checkpoint then
looked done while the one file the readers need was missing. A file rather than a
heredoc because `next` is free text — backticks and quotes inside a shell string
are executed, not written.

The name the hook gives it is signed by the machine **and** by the session:
`.claude/.checkpoint-<hostname>-<8 chars of session_id>`. **The host** — the
`.claude/` directory is sometimes synced between machines, and a checkpoint from
one would permit compaction on the other. **The session** — there can be several
terminals, and with one file per host the second `/checkpoint` overwrote the
state of the first, and the guard then guarded somebody else's.

To check yourself without waiting for compaction:

```bash
SID=$(state --self | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "{\"trigger\":\"manual\",\"cwd\":\"$PWD\",\"session_id\":\"$SID\"}" \
  | python ~/.claude/hooks/checkpoint.py guard
echo $?   # 0 — compaction allowed, 2 — the guard did not accept the checkpoint
```

## Step 6 — tell the owner

In one or two sentences: what was recorded, where it lies, where we continue
from. Repeat the session-duration warning if step 2b raised one. After that
`/compact` is safe.

Not a session close, no commit prompt, no memory saves — that is `/end`.

## Who reads the pointer

Two independent readers, and both take it from the same place.

**The hook `~/.claude/hooks/checkpoint.py`** — on this machine, for any session.
`PreCompact` does not let compaction through without a checkpoint or with one
older than half an hour; `PostCompact` files the summary into
`.claude/compact-summaries/`; `UserPromptSubmit` returns the pointer to the
context — but only with the human's next message.

**The Tausozavr service** — for sessions brought up under it. Its `PreCompact`
hook asks the service for the summary instructions; the service puts the
record's path and the `next` from the pointer into them, the list of
uncommitted files and the session's files, and after compaction it sends a
message of its own — carry on from that step — so there is no waiting for the
human. It does not repeat its own checkpoint refusal when the host hook is in
place: two texts about one trouble read as two troubles.

Three consequences for the work follow:

- **Under the service, the service dictates the summary's structure.** There is
  no need to write your own instructions after `/compact` there: they argue with
  what has been dictated. In a session from a terminal it is the other way
  round — the text after `/compact` is the only channel, the host hook puts
  nothing into the summary.
- **The guard stands only on manual compaction.** Automatic compaction, at the
  ceiling of the window, is never blocked: no checkpoint will be asked for
  there. Which means recording the state has to happen in advance, not at the
  very limit.
- **Compaction does not restart the session.** Measured 22.08 on both a worker
  and a supervisor: `session_id`, the process and the transcript stay, and most
  of the prefix comes back from cache. So «Где я сейчас» is written for a
  session that continues, not for a stranger — but write it as if for a
  stranger anyway: the conversation itself will be gone.

## Gotchas

- **Skipping step 5 is the only way to fail this skill silently.** Everything
  else leaves a trace on disk; the pointer is what the readers look for, and
  without it they report «no checkpoint» while the work sits written and unseen.
- **Do not write the pointer by hand** — `checkpoint.py save`. Written by hand it
  is a project write, and TAUSIK's Rule 1 refuses it as soon as the last task is
  closed. Verified 27.08.2026: `Write` and `echo >` both blocked, the hook's own
  write allowed.
- **A stale timestamp reads as «not written».** The hook stamps `at` itself for
  exactly that reason; anything you type there can only be wrong.
- **Editing the record, replace the whole block up to the next heading.** A tail
  cut once erased 291 lines of the chronicle. `replace_symbol_body` on the
  heading cannot overshoot; «from here to the end of file» can.
- **The handoff JSON breaks quietly.** One unescaped quote inside a value and
  the call fails while the checkpoint still looks written.
- **The guard stands only on manual compaction.** The automatic one, at the
  ceiling of the window, is never blocked — so the state is recorded in advance,
  not at the limit.
- **Reading the plan whole costs more than the compaction did.** 98 600 tokens
  against 1 100 for the section you actually need, and the whole file is then
  paid for on every turn that follows.

## Rules

1. Do not invent progress. Only what is done and verified gets marked done.
2. Do not rewrite the record to fit what came out. A divergence goes into the
   log, not retroactively into the statement of the task.
3. Something long-lived about how the project is built came to light — offer to
   put it into memory, but do not put it there without a «да».
4. The plan and the TAUSIK database are two halves of one record, not two
   records. What went into one goes into the other, or they drift — and the
   drift is discovered by whoever is misled by it.
