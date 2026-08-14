"""The terminal screen of the run dashboard — painting only.

Split out of `autoloop_tui` along the seam that module's docstring already
declared: `collect()` turns files on disk into a plain dict, and this file only
paints that dict. Nothing here reads the project, so nothing here can be wrong
about the numbers; and the textual import stays where a headless run never
reaches it.
"""

from __future__ import annotations

import autoloop_journal as journal
import autoloop_tui as tui

REFRESH_SECONDS = 1.0


def run_dashboard(project_dir: str, config: dict | None = None) -> int:
    """Open the live screen. Falls back to a single text render when textual is
    missing or stdout is not a terminal (piping `watch` into a file is a
    reasonable thing to do and must not explode)."""
    import sys

    try:
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal
        from textual.widgets import Footer, Static
    except ImportError:
        print(tui.render_text(tui.collect(project_dir, config)))
        return 0

    if not sys.stdout.isatty():
        print(tui.render_text(tui.collect(project_dir, config)))
        return 0

    class Dashboard(App):
        CSS = """
        Screen { background: $surface; }
        #cat { width: 22; content-align: center middle; color: $warning; }
        #body { padding: 1 2; }
        .row { height: auto; }
        #detail { height: 1fr; border-top: solid $primary; padding: 1 2; }
        """
        BINDINGS = [("q", "quit", "выход"), ("d", "toggle_detail", "подробно")]

        def __init__(self) -> None:
            super().__init__()
            self.tick = 0
            self.show_detail = False

        def compose(self) -> ComposeResult:
            with Horizontal(classes="row"):
                yield Static(id="cat")
                yield Static(id="body")
            yield Static(id="detail")
            yield Footer()

        def on_mount(self) -> None:
            self.set_interval(REFRESH_SECONDS, self.refresh_data)
            self.refresh_data()

        def action_toggle_detail(self) -> None:
            self.show_detail = not self.show_detail
            self.refresh_data()

        def refresh_data(self) -> None:
            self.tick += 1
            data = tui.collect(project_dir, config)
            self.query_one("#cat", Static).update(tui.cat_frame(data["status"], self.tick))
            self.query_one("#body", Static).update(body_markup(data))
            self.query_one("#detail", Static).update(
                detail_markup(data) if self.show_detail else "d — подробности прогона"
            )

    Dashboard().run()
    return 0


def body_markup(data: dict) -> str:
    tokens = data["tokens"]
    done, active = len(data["tasks_done"]), len(data["tasks_active"])
    total = data["tasks_total"]
    percent = data["percent"]
    over = percent is not None and percent >= data["soft_threshold"]
    return (
        f"[b]autoloop[/b] · {data['caption']}"
        f"{'  · итерация ' + str(data['iteration']) if data['iteration'] else ''}\n"
        f"[dim]задача[/dim]   {data['current_task'] or '—'}\n"
        f"[dim]задачи[/dim]   {tui.progress_bar(done, active, total)}"
        f"  {tui.progress_label(done, active, total)}\n"
        f"[dim]контекст[/dim] {'[yellow]' if over else ''}{tui.bar((percent or 0) / 100)}"
        f"  {tui.format_percent(percent)}{'[/yellow]' if over else ''}"
        f" [dim](порог {int(data['soft_threshold'])}%)[/dim]\n"
        f"[dim]работа[/dim]   [b]{journal.format_tokens(journal.work_tokens(tokens))}[/b]"
        f" [dim]за прогон · выход {journal.format_tokens(tokens['output'])}"
        f" · запись кэша {journal.format_tokens(tokens['cache_write'])}[/dim]\n"
        f"[dim]время[/dim]    {tui.format_elapsed(data['elapsed_seconds'])}"
        f" [dim]· ${data['cost_usd']:.2f}[/dim]"
    )


def detail_markup(data: dict) -> str:
    lines = []
    if data["tasks_queued"]:
        lines.append("[b]дальше:[/b] " + ", ".join(data["tasks_queued"]))
    if data["tasks_done"]:
        lines.append("[b]готово:[/b] " + ", ".join(data["tasks_done"]))
    for entry in data["entries"][-6:]:
        lines.append(
            f"  #{entry.get('iteration')} {entry.get('task_slug')}: "
            f"{entry.get('status_before')} → {entry.get('status_after')}, "
            f"{entry.get('exit_reason')}, "
            f"контекст {tui.format_percent(entry.get('percent_at_exit'))}"
        )
    return "\n".join(lines) or "журнал пуст"
