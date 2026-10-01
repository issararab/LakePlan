"""
Per-request debug step collector.

Any module can call add_step() during a request. The agent starts a collection
at the top of handle() and reads it back at the end to attach to ChatResponse.
Uses ContextVar so it is coroutine-safe and requires no thread locals.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Literal

StepType = Literal["phase", "llm", "extraction", "rule", "transition", "plan", "sql", "error"]

_collector: ContextVar[list[dict] | None] = ContextVar("debug_collector", default=None)


def start() -> list[dict]:
    """Create a fresh collector for this request. Returns the list."""
    steps: list[dict] = []
    _collector.set(steps)
    return steps


def stop() -> None:
    _collector.set(None)


def add(label: str, detail: str, step_type: StepType = "llm", full_detail: str | None = None) -> None:
    """Append a step if a collector is active (noop otherwise).

    detail      — short summary shown collapsed (≤200 chars recommended)
    full_detail — full content shown when the step is expanded in the UI
    """
    steps = _collector.get()
    if steps is not None:
        entry: dict = {"label": label, "detail": detail, "type": step_type}
        if full_detail and full_detail != detail:
            entry["full_detail"] = full_detail
        steps.append(entry)
