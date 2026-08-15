"""Which conversation this watcher is watching, and whether anyone is in it.

Split out of the watcher because this is where it went blind. Every wrong
answer here is silent: the watcher keeps polling, keeps logging its start line,
and simply stops being able to tell a person mid-sentence from an empty room —
the one distinction the whole mechanism rests on.

Two rules, both learned from a live incident:

  * the folder is found by exact name, never by a `*slug*` match — the scratchpad
    projects created inside a project carry its whole slug in their own names;
  * "no transcript" is `None`, not "quiet forever" — not finding a chat is not
    evidence that nobody is in it.
"""

from __future__ import annotations

import glob
import json
import os
import re
import time

SESSION_FILE = os.path.join(".tausik", ".chat.session")
# Where the mechanism's own processes announce themselves, so they are not
# mistaken for the agent's background work. A registry rather than a name
# check: `python overlay` and `python -m pytest` are the same executable.
OWN_FILE = os.path.join(".tausik", ".autoloop-own.json")
NOT_SLUG = re.compile(r"[^A-Za-z0-9]")


def project_slug(project_dir: str) -> str:
    """The folder name Claude Code gives this project under ~/.claude/projects.

    Everything that is not a letter or a digit becomes a dash — `_` included.
    The old replace covered only `:` `\\` `/`, so `D:\\Claude_mcp` searched for
    `D--Claude_mcp` while the transcripts sat in `D--Claude-mcp`. Nothing
    matched, and with nothing matched the watcher lost its only way of knowing
    whether a human was typing.
    """
    return NOT_SLUG.sub("-", os.path.abspath(project_dir))


def transcript_dir(project_dir: str):
    """This project's transcript folder, matched by exact name.

    Never a `*slug*` glob: a scratchpad project created inside this one carries
    the whole slug in its own name, and the newest file across that match
    belongs to a different chat.
    """
    path = os.path.expanduser(os.path.join("~", ".claude", "projects", project_slug(project_dir)))
    return path if os.path.isdir(path) else None


def newest_transcript(folder):
    if not folder:
        return None
    newest, newest_mtime = None, -1.0
    for path in glob.glob(os.path.join(folder, "*.jsonl")):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime > newest_mtime:
            newest, newest_mtime = path, mtime
    return newest


def current_session(project_dir: str):
    """The transcript this session was given by the last SessionStart hook.

    Returned whether or not the file is on disk yet: Claude Code creates it on
    the first message, so a fresh session has the name minutes before the file.
    Dropping the name for that reason sent every session start down the guessing
    path — and the guess was wrong. Outlives `known`: after a wipe the
    conversation continues in a different file.
    """
    try:
        with open(os.path.join(project_dir, SESSION_FILE), encoding="utf-8") as f:
            path = f.read().strip()
    except OSError:
        return None
    return path or None


def transcript_path(project_dir: str, known: str | None = None):
    """This session's log — the path, which may not exist yet."""
    return current_session(project_dir) or known or newest_transcript(transcript_dir(project_dir))


def transcript_size(project_dir: str, known: str | None = None) -> int:
    """How much this session's log holds right now, or 0 when it cannot be read.

    The one signal that says "the chat took the command and started working"
    without waiting for it to finish. A turn can run for an hour; this moves
    within seconds of the first token.
    """
    path = transcript_path(project_dir, known)
    if not path:
        return 0
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def idle_seconds(path, now=None):
    """How long the conversation has been quiet, or None for "no idea".

    A missing transcript used to answer "quiet forever", which is the most
    dangerous thing this function can say: it reports an empty room about a
    chat it merely failed to find. Unknown is its own answer, and callers are
    expected to treat it as a person present — a skipped cleanup costs one full
    window, a wrongly timed one costs somebody's conversation.
    """
    now = time.time() if now is None else now
    if not path:
        return None
    try:
        return max(0.0, now - os.path.getmtime(path))
    except OSError:
        return None


def register_own(project_dir: str, pid: int | None = None) -> None:
    """Announce a process as part of the mechanism. Never raises: a registry
    that cannot be written costs a delayed cleanup, not a broken run."""
    pid = os.getpid() if pid is None else pid
    path = os.path.join(project_dir, OWN_FILE)
    known = _read_own(path)
    known.add(int(pid))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(known), f)
    except OSError:
        pass


def _read_own(path: str) -> set[int]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return set()
    return {int(p) for p in data if isinstance(p, int)} if isinstance(data, list) else set()


def own_pids(project_dir: str, is_alive=None) -> set[int]:
    """The mechanism's live processes.

    Dead entries are dropped on read rather than trusted: a registry that only
    ever grows would eventually name a recycled pid and silence the watcher for
    good — the failure mode that is worse than the one this exists to fix.
    """
    known = _read_own(os.path.join(project_dir, OWN_FILE))
    if is_alive is None:
        return known
    return {pid for pid in known if is_alive(pid)}


def descendants(pid: int, table: dict) -> set[int]:
    """Every process under `pid`, however deep, from one snapshot.

    Depth matters: a background command is `chat -> shell -> python`, and a
    check that looked only at direct children would call that quiet.
    """
    children: dict[int, list[int]] = {}
    for child, entry in table.items():
        parent = entry[0] if isinstance(entry, (tuple, list)) else entry
        children.setdefault(parent, []).append(child)
    seen: set[int] = set()
    queue = list(children.get(pid, ()))
    while queue:
        current = queue.pop()
        if current in seen or current == pid:
            continue
        seen.add(current)
        queue.extend(children.get(current, ()))
    return seen


def started_at(pid: int) -> float | None:
    """Unix time this process started, or None when it cannot be asked."""
    try:
        import ctypes
        import ctypes.wintypes as wintypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            created, exited, kern, user = (wintypes.FILETIME() for _ in range(4))
            if not kernel32.GetProcessTimes(
                handle, *(ctypes.byref(t) for t in (created, exited, kern, user))
            ):
                return None
            ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
            return ticks / 1e7 - 11644473600  # 100ns since 1601 -> unix seconds
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 — an unaskable process is "not proven new"
        return None


# A process that came up with the chat is part of its furniture, not its work.
BOOT_GRACE_SECONDS = 120.0


def background_pids(
    chat_pid: int | None,
    table: dict,
    own: set[int] | None = None,
    age_of=None,
    grace: float = BOOT_GRACE_SECONDS,
) -> set[int]:
    """Work the agent STARTED, as opposed to everything hanging off the chat.

    Two exclusions, both learned by measuring a real chat rather than guessing:

    * the mechanism's own processes — watcher, overlay, dashboard — by
      REGISTERED pid, because a name check cannot tell `python overlay` from
      `python -m pytest`;
    * everything that came up with the chat itself. A live chat here had 43
      descendants: every MCP server, all aged exactly as the chat (143 min),
      against a background command aged 0. Counting those as work would mean
      the window is never quiet and never cleaned — worse than the defect this
      answers.

    A process whose start time cannot be read is NOT counted as work: the
    conservative direction here is to let a cleanup happen, not to block it
    forever on something unreadable.
    """
    if not chat_pid:
        return set()
    kids = descendants(chat_pid, table) - (own or set())
    age_of = started_at if age_of is None else age_of
    chat_started = age_of(chat_pid)
    if chat_started is None:
        return kids  # unknown chat age: fall back to "everything under it counts"
    fresh = set()
    for pid in kids:
        born = age_of(pid)
        if born is not None and born > chat_started + grace:
            fresh.add(pid)
    return fresh
