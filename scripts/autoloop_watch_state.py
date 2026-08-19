"""What the watcher is doing right now, written down where a window can read it.

The watcher already knew all of this — it logs «жду тишины», «фоновая работа,
уборка ждёт», «окно на 58% … жду 15 с» into `chat-watch.log`. Nobody opens a
log file to answer "why is it showing that number?", so the window showed a
joke instead and the human asked the question out loud, twice.

A state file rather than the log: the log is a story, and reading a story
backwards to find the current state is how a reader ends up reporting a line
from ten minutes ago as now. This carries a timestamp for exactly that reason —
a state nobody refreshed is not the present, and `read` says so by returning
None rather than the last thing it saw.

Separate module because `autoloop_watch` sits at 381 lines against a 400-line
cap: the writer would have fitted, the writer plus the wording would not.
"""

from __future__ import annotations

import json
import os
import time

STATE_FILE = os.path.join(".tausik", ".chat-watch.state.json")
# A tick is 2 seconds; three missed ticks and the watcher is gone, not slow.
MAX_AGE = 15.0

PHASE_BLIND = "blind"
PHASE_BUSY = "busy"
PHASE_ARMING = "arming"
PHASE_WAITING = "waiting"
PHASE_WATCHING = "watching"

# The plaque sizes itself to its text, so this is about staying readable next to
# a percentage, not about a hard limit.
MAX_WORKER_NAME = 22


PHASE_COOLING = "cooling"  # over the threshold, but a refusal is still holding
PHASE_WINDING = "winding"  # the run was asked to finish up; the wipe waits for it


def phase_of(
    *,
    blind: bool,
    busy: bool,
    arming: bool,
    percent=None,
    threshold=None,
    cooling: float = 0.0,
    winding: bool = False,
) -> str:
    """Which state the watcher is in, in priority order.

    Blindness first: it outranks everything because in that state nothing else
    the watcher believes is worth acting on. Then the two reasons a cleanup is
    NOT happening (work in flight, countdown already running), then the third —
    a refusal still holding — and only then whether the window is over the
    threshold at all.

    Winding outranks arming because it is the later half of the same countdown:
    the request to finish up has already gone, and «взвожу уборку» would say
    the opposite of what is happening.

    Cooling ranks below both because it is what «waiting» used to be mistaken
    for: over the threshold, quiet, and still not cleaning.
    """
    if blind:
        return PHASE_BLIND
    if busy and not winding:
        return PHASE_BUSY
    if winding:
        return PHASE_WINDING
    if arming:
        return PHASE_ARMING
    over = percent is not None and threshold is not None and percent >= threshold
    if over and cooling > 0:
        return PHASE_COOLING
    if over:
        return PHASE_WAITING
    return PHASE_WATCHING


def phrase(
    phase: str,
    *,
    quiet=None,
    percent=None,
    threshold=None,
    workers: int = 0,
    worker: str = "",
    cooling: float = 0.0,
) -> str:
    """One line for a small window: what is happening and what it is waiting for.

    Deliberately says the REASON, not the verdict. «жду тишины» answers the
    question the number alone provoked; «уборки не будет» without a reason just
    moves the question one step along.

    The waited-for process is NAMED because the count alone did not answer the
    question either: «1 проц.» sent the human looking for an agent that was not
    there twice in one run, while the real answer was the graph server's own
    index worker.
    """
    if phase == PHASE_BLIND:
        return "не вижу чат — уборки не будет"
    if phase == PHASE_BUSY:
        if worker:
            name = worker[:MAX_WORKER_NAME]
            more = f" +{workers - 1}" if workers > 1 else ""
            return f"жду фоновую работу: {name}{more}"
        return f"жду фоновую работу · {workers} проц." if workers else "жду фоновую работу"
    if phase == PHASE_ARMING:
        return "взвожу уборку — говори, и отменю"
    if phase == PHASE_WINDING:
        return "просил свернуть задачу — жду, пока прогон встанет"
    if phase == PHASE_COOLING:
        return f"уборку отменили — предложу снова через {int(cooling)} с"
    if phase == PHASE_WAITING:
        return f"жду тишины · {int(quiet)} с" if quiet is not None else "жду тишины"
    if percent is not None and threshold is not None:
        return f"смотрю: {percent}% из {int(threshold)}%"
    return "смотрю"


def write(project_dir: str, phase: str, detail: str, **fields) -> bool:
    """Replace the state file. Failure is silent — a watcher that cannot
    describe itself still watches, and taking the run down to say so would be
    the worse trade."""
    payload = {"ts": time.time(), "phase": phase, "detail": detail, **fields}
    path = os.path.join(project_dir, STATE_FILE)
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        return False
    return True


def observe(
    project_dir: str,
    *,
    blind: bool,
    busy: bool,
    arming: bool,
    percent=None,
    threshold=None,
    quiet=None,
    workers: int = 0,
    worker: str = "",
    cooling: float = 0.0,
    winding: bool = False,
) -> bool:
    """Record this tick: the watcher reports FACTS, this module names them.

    One call rather than phase-then-phrase-then-write at the call site, because
    the caller is a 400-line-capped loop and because the naming should be
    decided in one place — the window and any future reader must not each
    invent their own wording for the same state.
    """
    phase = phase_of(
        blind=blind,
        busy=busy,
        arming=arming,
        percent=percent,
        threshold=threshold,
        cooling=cooling,
        winding=winding,
    )
    detail = phrase(
        phase,
        quiet=quiet,
        percent=percent,
        threshold=threshold,
        workers=workers,
        worker=worker,
        cooling=cooling,
    )
    return write(project_dir, phase, detail, percent=percent)


def read(project_dir: str, max_age: float = MAX_AGE, now=None) -> dict | None:
    """The current state, or None when there isn't one.

    None covers three different situations on purpose — no run, no watcher, a
    watcher that stopped refreshing — because for a reader they are the same
    fact: nobody is describing this moment. Reporting a stale line as the
    present is the failure this whole file exists to avoid.
    """
    now = time.time() if now is None else now
    path = os.path.join(project_dir, STATE_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    ts = data.get("ts")
    if not isinstance(ts, (int, float)) or (now - ts) > max_age:
        return None
    return data


def clear(project_dir: str) -> None:
    """Leave nothing behind: a state file that outlives its watcher is the
    stale-pointer defect again, one file over."""
    try:
        os.remove(os.path.join(project_dir, STATE_FILE))
    except OSError:
        pass
