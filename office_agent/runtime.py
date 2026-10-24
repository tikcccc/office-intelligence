"""Per-run deadlines, cancellation and accounting, propagated explicitly into worker threads."""

import threading
import time
from concurrent.futures import Executor
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from uuid import uuid4


class DeadlineExceeded(TimeoutError):
    pass


@dataclass
class RunContext:
    deadline: float
    run_id: str = field(default_factory=lambda: uuid4().hex)
    cancelled: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)
    calls: list[dict] = field(default_factory=list)
    closed: bool = False
    offline: bool = False
    allowed_writes: frozenset[str] = field(default_factory=frozenset)
    allow_review: bool = True
    live_only: bool = False
    source_data: dict | None = None
    parent: "RunContext | None" = None

    def check(self) -> None:
        if self.parent:
            self.parent.check()
        if self.closed or self.cancelled.is_set() or time.monotonic() >= self.deadline:
            raise DeadlineExceeded("Run deadline reached; no further work or writes allowed.")

    def remaining(self) -> float:
        self.check()
        own = self.deadline - time.monotonic()
        return min(own, self.parent.remaining()) if self.parent else own


_current: ContextVar[RunContext | None] = ContextVar("office_agent_run", default=None)


def current() -> RunContext | None:
    return _current.get()


def live_only() -> bool:
    context = current()
    while context:
        if context.live_only:
            return True
        context = context.parent
    return False


def source_data(name: str):
    context = current()
    while context:
        if context.source_data is not None:
            return context.source_data.get(name)
        context = context.parent
    return None


@contextmanager
def scope(context: RunContext):
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)


def checkpoint() -> None:
    if context := current():
        context.check()


def timeout(default: float) -> float:
    checkpoint()
    context = current()
    return max(0.001, min(default, context.remaining())) if context else default


def submit(pool: Executor, fn, *args, **kwargs):
    """A fresh copied context per submission; Context objects cannot run concurrently."""
    return pool.submit(copy_context().run, fn, *args, **kwargs)


@contextmanager
def write_guard():
    """Serialize cancellation and the final write/commit check within a run."""
    context = current()
    if context:
        with context.lock:
            context.check()
            yield
    else:
        yield
