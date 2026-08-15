"""Stopping conditions for the autonomous loop.

Kept apart from the driver so they can be reasoned about — and tested — without
spawning a process. Every brake answers one question: is there any reason not
to start another iteration?

A loop without brakes is not autonomy, it is an unattended way to spend money:
nobody is watching, so "keeps going" and "stuck in a circle" look identical
from outside.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STOP_FILENAME = ".autoloop.stop"


@dataclass
class Verdict:
    stop: bool
    reason: str = ""
    clean: bool = True  # clean=False means the run ended badly (nonzero exit)


@dataclass
class BrakeState:
    """Mutable tally the driver updates after every iteration."""

    max_iterations: int = 20
    max_idle_iterations: int = 3
    max_crash_streak: int = 2
    iteration: int = 0
    idle_streak: int = 0
    crash_streak: int = 0
    total_cost_usd: float = 0.0
    last_task: str | None = None
    history: list[dict] = field(default_factory=list)

    @classmethod
    def from_config(cls, config: dict, **overrides) -> "BrakeState":
        """Config supplies the thresholds; explicit CLI flags win over it.

        An override of None means "flag not given" — otherwise argparse's
        defaults would silently shadow every configured value.
        """
        values: dict[str, Any] = {
            "max_iterations": int(config.get("max_iterations") or 20),
            "max_idle_iterations": int(config.get("max_idle_iterations") or 3),
            "max_crash_streak": int(config.get("max_crash_streak") or 2),
        }
        for key, value in overrides.items():
            if value is not None and key in values:
                values[key] = int(value)
        # A non-positive limit disables the brake it belongs to, which is the
        # one configuration mistake that turns the loop into a runaway.
        for key, floor in (
            ("max_iterations", 1),
            ("max_idle_iterations", 1),
            ("max_crash_streak", 1),
        ):
            if values[key] < floor:
                values[key] = floor
        return cls(**values)

    def record(
        self, *, task_slug: str, crashed: bool, progressed: bool, cost_usd: float = 0.0
    ) -> None:
        self.last_task = task_slug
        self.total_cost_usd += cost_usd
        self.crash_streak = self.crash_streak + 1 if crashed else 0
        self.idle_streak = 0 if progressed else self.idle_streak + 1
        self.history.append(
            {
                "iteration": self.iteration,
                "task": task_slug,
                "crashed": crashed,
                "progressed": progressed,
                "cost_usd": cost_usd,
            }
        )


def stop_switch_path(project_dir: Path | str) -> Path:
    return Path(project_dir) / ".tausik" / STOP_FILENAME


def clear_stop_switch(project_dir: Path | str) -> tuple[bool, str]:
    """Drop a switch left behind by a run that has already ended.

    The switch says "stop the run that is going". Once that run is over the
    file outlives its meaning, and the next supervisor reads it as an order
    meant for itself: it starts, checks the brakes, and leaves — a run that
    dies at the first step for a reason belonging to yesterday.

    Returns (cleared, error). A missing file is the normal case, not a
    failure. An unremovable one is: the caller must refuse to start rather
    than walk into that exact death.
    """
    try:
        stop_switch_path(project_dir).unlink()
    except FileNotFoundError:
        return False, ""
    except OSError as e:
        return False, str(e)
    return True, ""


def check_brakes(project_dir: Path | str, state: BrakeState) -> Verdict:
    """Consulted before every iteration — never mid-flight.

    Finishing the current iteration before honouring the kill switch is
    deliberate: killing an agent mid-edit strands half-written work, which
    costs more to untangle than the extra minute of waiting.
    """
    if stop_switch_path(project_dir).exists():
        return Verdict(True, "найден файл .tausik/.autoloop.stop — остановка по требованию")

    if state.iteration >= state.max_iterations:
        return Verdict(
            True,
            f"достигнут предел итераций ({state.max_iterations}) — "
            "цикл остановлен, чтобы не уходить в бесконечный прогон",
            clean=False,
        )

    if state.crash_streak >= state.max_crash_streak:
        return Verdict(
            True,
            f"{state.crash_streak} подряд неудачных запусков claude — "
            f"последняя задача: {state.last_task}",
            clean=False,
        )

    if state.idle_streak >= state.max_idle_iterations:
        return Verdict(
            True,
            f"{state.idle_streak} итерации подряд без прогресса — "
            f"цикл топчется на задаче {state.last_task}",
            clean=False,
        )

    return Verdict(False)
