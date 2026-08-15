"""The cat's voice: one short line about what is happening right now.

The overlay already shows the state as numbers. Numbers tell you where the run
is; a line tells you what just happened — a task closed, an iteration fell
over, the context is filling up. That is the difference between reading a
dashboard and glancing at a corner of the screen.

Pure by construction. It takes the dict `autoloop_tui.collect()` returns and
nothing else: no files, no DB, no network, and no clock — the caller passes
`now`, so a test can move time without waiting for it. The only state is
process-local and exists for one reason: a line has to stay on screen long
enough to be read, and must not repeat itself back to back.

Lines are data (`QUIPS`), not literals buried in branches — a new one is a
tuple entry, not a code change.
"""

from __future__ import annotations

import random

import autoloop_tui as tui

REASON_START = "start"
REASON_WORKING = "working"
REASON_TASK_DONE = "task_done"
REASON_CONTEXT_HIGH = "context_high"
REASON_FAILED = "failed"
REASON_STOPPED = "stopped"
REASON_IDLE = "idle"

# Reasons about an event rather than a state. They survive the tick that
# produced them, otherwise "закрыл задачу" would flash for one second and be
# gone before anyone looked up.
STICKY_REASONS = frozenset({REASON_TASK_DONE})

HOLD_SECONDS = 7.0
QUIP_MAX_CHARS = 46

QUIPS: dict[str, tuple[str, ...]] = {
    REASON_START: (
        "разминаю лапы",
        "так, где тут очередь",
        "заваривай чай, я начал",
        "открываю глаза",
    ),
    REASON_WORKING: (
        "грызу {task}",
        "копаю {task}",
        "{task}, не убегай",
        "разбираюсь с {task}",
        "{task} — моя добыча",
    ),
    REASON_TASK_DONE: (
        "минус одна задача",
        "закрыл, несите ещё",
        "готово. кто следующий",
        "одной меньше — мур",
    ),
    REASON_CONTEXT_HIGH: (
        "окно пухнет, закругляюсь",
        "контекст на исходе, пишу handoff",
        "многовато букв, сворачиваюсь",
        "пора передавать смену",
    ),
    REASON_FAILED: (
        "итерация упала, смотри журнал",
        "уронил, признаю",
        "что-то пошло не так",
        "разбор полётов в отчёте",
    ),
    REASON_STOPPED: (
        "сплю. /auto разбудит",
        "лапы сложил, жду команды",
        "тишина. можно и поспать",
        "очередь пуста, я в домике",
    ),
    REASON_IDLE: (
        "перевожу дух",
        "жду следующую",
        "смотрю в окно",
        "пауза",
    ),
}


def reason_for(data: dict | None, task_just_closed: bool = False) -> str:
    """Why the cat is about to speak. Total: any snapshot maps to a reason."""
    data = data or {}
    if task_just_closed:
        return REASON_TASK_DONE

    status = data.get("status")
    if status == tui.STATUS_FAILED:
        return REASON_FAILED
    if status == tui.STATUS_RUNNING:
        percent = data.get("percent")
        threshold = data.get("soft_threshold") or 30
        if isinstance(percent, (int, float)) and percent >= threshold:
            return REASON_CONTEXT_HIGH
        return REASON_WORKING if _task_of(data) else REASON_START
    if status == tui.STATUS_STOPPED:
        return REASON_STOPPED
    # Unknown status included: an unrecognised state is a quiet one, never a
    # crash — the window must outlive whatever produced it.
    return REASON_IDLE


def ellipsize(text: str, limit: int = QUIP_MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _task_of(data: dict) -> str:
    return str(data.get("current_task") or "").strip()


class QuipPicker:
    """Keeps the line that is currently on screen.

    State lives here rather than in the caller because two of the rules are
    about time: a line holds for `hold_seconds`, and the same reason never
    repeats the same line twice in a row.
    """

    def __init__(self, hold_seconds: float = HOLD_SECONDS, rng=None):
        self._hold = hold_seconds
        self._rng = rng or random.Random()
        self._reason: str | None = None
        self._text = ""
        self._shown_at: float | None = None
        self._last_by_reason: dict[str, str] = {}
        self._done_count: int | None = None
        self._sticky: tuple[str, float] | None = None

    def update(self, data: dict | None, now: float) -> str:
        """The line to paint. Call it as often as you like — it changes when
        the news changes, or once the current line has had its time."""
        data = data or {}
        reason = self._reason_now(data, now)
        expired = self._shown_at is None or (now - self._shown_at) >= self._hold
        if reason != self._reason or expired:
            self._text = self._pick(reason, data)
            self._reason = reason
            self._shown_at = now
        return self._text

    def _reason_now(self, data: dict, now: float) -> str:
        done = data.get("tasks_done") or []
        count = len(done) if isinstance(done, (list, tuple)) else 0
        just_closed = self._done_count is not None and count > self._done_count
        self._done_count = count

        reason = reason_for(data, task_just_closed=just_closed)
        if reason in STICKY_REASONS:
            self._sticky = (reason, now + self._hold)
        elif self._sticky and now < self._sticky[1]:
            reason = self._sticky[0]
        else:
            self._sticky = None
        return reason

    def _pick(self, reason: str, data: dict) -> str:
        task = _task_of(data)
        options = QUIPS.get(reason) or QUIPS[REASON_IDLE]
        # A line naming a task is only offered when there is a task to name:
        # the cat reports, it does not invent.
        kept: list[str] = [line for line in options if task or "{task}" not in line]
        options = tuple(kept)
        if not options:
            return ""
        previous = self._last_by_reason.get(reason)
        choices = [line for line in options if line != previous] or options
        template = self._rng.choice(choices)
        self._last_by_reason[reason] = template
        return ellipsize(template.replace("{task}", task))
