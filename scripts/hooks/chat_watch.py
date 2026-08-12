"""SessionStart hook: bring up the context watcher for this chat.

The point of the whole design is that the human runs `claude` and nothing
else. So the watcher has to start itself, and this is the only place that
knows a session just began.

Must return instantly: a hook that thinks holds up the chat's startup. It
spawns a detached process and exits.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

MAX_HOPS = 6


def process_table():
    """{pid: (parent_pid, exe_name)} — one snapshot, no external commands.

    A hook has a five-second budget; shelling out to tasklist would eat most
    of it, and every such call flashes a console window at the user.
    """
    try:
        import autoloop_keys

        return autoloop_keys.process_table()
    except ImportError:
        return {}


def owning_chat(pid, table, hops=MAX_HOPS):
    """Walk up from this hook to the chat that started it.

    Not "the only claude.exe running": there may be several, and typing into
    the wrong one is the failure this whole mechanism must never have.
    """
    for _ in range(hops):
        parent, _name = table.get(pid, (None, ""))
        if parent is None:
            return None
        name = table.get(parent, (None, ""))[1]
        if name.startswith("claude"):
            return parent
        pid = parent
    return None


def payload() -> dict:
    try:
        import json

        return json.loads(sys.stdin.read() or "{}")
    except Exception:  # noqa: BLE001 — a malformed payload means "no payload"; a hook must never fail the session start
        return {}


def mark_started(project_dir: str, transcript=None) -> None:
    """`/clear` leaves no other trace.

    It ends no turn, so the Stop flag never comes; the watcher has nothing to
    wait on and used to guess with a timer, which cost a run (dead end #19).
    A session that has come up is the one fact only this hook knows.

    The transcript goes to a second, non-consumed file: after a wipe the
    watcher must follow the new conversation, and a watcher still reading the
    old file sees a chat that never speaks — the state in which it wipes
    somebody mid-sentence.
    """
    try:
        with open(
            os.path.join(project_dir, ".tausik", ".chat.started"), "w", encoding="utf-8"
        ) as f:
            f.write(str(os.getpid()))
    except OSError:
        pass
    if not transcript:
        return
    try:
        with open(
            os.path.join(project_dir, ".tausik", ".chat.session"), "w", encoding="utf-8"
        ) as f:
            f.write(transcript)
    except OSError:
        pass


def watch_enabled(project_dir: str) -> bool:
    """Has this project asked for its context to be cleaned?

    Off unless `.tausik/config.json` says `autoloop.watch: true`. The hook ships
    to every project the library touches, and a chat that starts wiping its own
    conversation because a library update arrived is not a feature anyone
    consented to. Opting in is one line; opting out after the fact is a lost
    conversation.
    """
    try:
        import json

        from tausik_utils import tausik_config_path

        with open(tausik_config_path(project_dir), encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, ValueError, ImportError):
        return False
    if not isinstance(config, dict):
        return False
    return config.get("autoloop", {}).get("watch") is True


def main() -> int:
    data = payload()
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    enabled = watch_enabled(project_dir)
    # A watcher started by hand is also owed the mark; without it that watcher
    # waits out its timeout after every `/clear`.
    if enabled or os.path.exists(os.path.join(project_dir, ".tausik", ".chat-watch.lock")):
        mark_started(project_dir, data.get("transcript_path"))
    if not enabled:
        return 0
    if os.environ.get("TAUSIK_AUTONOMY") == "1":
        return 0  # the headless loop manages its own context, by exiting
    try:
        import autoloop_watch as watch
    except ImportError:
        return 0
    pid = owning_chat(os.getpid(), process_table())
    if not pid:
        watch.log(project_dir, "хук не нашёл процесс чата — наблюдатель не поднят")
        return 0
    # The payload names this session's transcript. Without it the watcher
    # would judge "is anyone talking?" by the newest file in the project —
    # which may belong to somebody else's chat entirely.
    watch.spawn(project_dir, pid, data.get("transcript_path"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
