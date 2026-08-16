---
name: commit
description: "Create a standardized git commit."
effort: fast
context: inline
---

# /commit — Git Commit

Create well-structured commits with conventional commit messages.
## Conventional Commit Format
```
<type>(<scope>): <description>

<body>

Co-Authored-By: Claude <noreply@anthropic.com>
```

## Algorithm

### 1. Analyze Changes
```bash
git status
git diff --stat
git diff --cached --stat
git log --oneline -5
```

### 2. Stage Files
- If changes already staged (`git diff --cached --stat` shows files) — use those
- If nothing staged — identify related unstaged changes from `git diff --stat`
- **NEVER stage** `.env`, credentials, secrets, large binaries
- **Prefer specific files** over `git add -A`
- Ask user if ambiguous which files to include

```bash
git add file1.py file2.py
git diff --cached --stat   # verify what's staged
```

### 3. Generate Commit Message
From the diff, determine:
- **type**: feat/fix/refactor/etc.
- **scope**: primary affected module
- **description**: concise imperative summary (max 72 chars)
- **body**: 1-3 sentences explaining WHY, not WHAT

### 4. Run Quality Gates
```bash
python scripts/gate_runner.py commit --files file1.py file2.py
```
- Get staged file list: `git diff --cached --name-only`
- Pass as space-separated arguments to `--files`
- Run from **project root** directory
- If a **blocking** gate fails (ruff, mypy) → show output, do NOT commit. Fix issues, re-stage with `git add`, re-run gates. Repeat until pass.
- If only **warnings** (filesize) → show them, proceed to confirm.

### 5. Present and Confirm
Show proposed message + file list + gate results. Ask user to confirm.

### 6. Commit
```bash
git commit -m "$(cat <<'EOF'
<type>(<scope>): <description>

<body>

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

### 7. Verify
```bash
git log --oneline -1
git status
```

### 8. Push (optional)

After a successful commit, ask the user: **"Push to remote? (y/n)"**

- If the user says yes (or originally asked to push):
  1. Determine the remote and branch: `git rev-parse --abbrev-ref HEAD`.
  2. Check if branch tracks a remote: `git rev-parse --abbrev-ref @{u} 2>/dev/null`.
  3. Authorize the push with a single-use ticket, then push — as **two
     separate shell calls**, never chained on one line:
  ```bash
  tausik push-ok
  ```
  then, as the next call:
  ```bash
  git push
  ```
  or if no upstream: `git push -u origin <branch>`.

  **Why two calls.** `git_push_gate` is a PreToolUse hook: it judges the
  command string *before* the shell runs any of it. A `push-ok` chained
  with `&&` has not executed at check time, so no ticket exists and the
  push is blocked — with a refusal that names a stale ticket or a
  directory `push-ok` never wrote to. That misreading has cost whole
  sessions; the fix is the call boundary, not the ticket path.

  `tausik push-ok` writes a 60-second TTL ticket bound to the current
  HEAD SHA; `git_push_gate` consumes it on the next push and re-blocks
  any subsequent push until you authorize again. The TTL is short — issue
  the push call immediately after, and re-run `push-ok` if a push fails
  and you retry, because a consumed ticket is gone.
- If the user says no — done, do not push.
- **NEVER force-push** unless the user explicitly asks.

## Rules
- **ALWAYS ask before committing** — never auto-commit
- **NEVER push** unless explicitly asked — when pushing, run `tausik push-ok` and `git push` as two separate calls, never chained on one line (the hook checks before the shell runs, so a chained `push-ok` has not written its ticket yet). The ticket is single-use, expires in 60s, and is bound to HEAD SHA
- **NEVER use --no-verify** or skip hooks
- **NEVER amend** unless user explicitly requests it
- If pre-commit hook fails: fix, re-stage, create NEW commit
- Keep description under 72 characters
- Use imperative mood: "add feature" not "added feature"
- **Suggest next:** "More tasks? `/task list` or `/end` to wrap up."

## Gotchas

- **Pre-commit hook failure does NOT create the commit** — after fixing, create a NEW commit, never `--amend` (amend would modify the previous, unrelated commit).
- **HEREDOC for commit messages** — always use `$(cat <<'EOF' ... EOF)` to avoid shell escaping issues with quotes and special characters in the body.
- **Never `git add -A`** in projects with `.env`, credentials, or large binaries — always stage specific files.
