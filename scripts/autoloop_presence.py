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
import os
import re
import time

SESSION_FILE = os.path.join(".tausik", ".chat.session")
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
