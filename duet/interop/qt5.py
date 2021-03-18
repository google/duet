import contextlib
import threading
from concurrent.futures import Future
from typing import Any, Awaitable, Callable, Iterator, Optional

import PyQt5.QtCore as QtCore

import duet
import duet.impl as impl

MILLIS = 1000


class Qt5Runner:
    def __init__(self, flush_timeout: float = 1):
        self.flush_timeout = flush_timeout

    @contextlib.contextmanager
    def run(self, func: Callable[..., Awaitable[Any]], *args, **kwds) -> Iterator[None]:
        with impl.Scheduler() as scheduler:

            def flush(ready_future: Future):
                if not ready_future.done():
                    scheduler.flush()

            def tick():
                scheduler.tick()
                if scheduler.active_tasks:
                    _call_later(self.flush_timeout, flush, scheduler.ready_future)
                    _add_qt_callback(scheduler.ready_future, tick)

            scheduler.spawn(func(*args, **kwds))
            tick()
            yield


def _call_later(delay, func, *args, **kw):
    QtCore.QTimer.singleShot(delay * MILLIS, lambda: func(*args, **kw))


def _add_qt_callback(future, func, *args, **kw):
    future.add_done_callback(lambda f: _call_later(func, *args, **kw))


def run(func: Callable[..., Awaitable[Any]], *args, **kwds) -> None:
    """Runs a duet coroutine function in the qt5 event loop."""
    return await Qt5Runner().run(func, *args, **kwds)
