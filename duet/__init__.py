# Copyright 2021 The Duet Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Run asynchronous coroutines using Futures.

Coroutines using async/await provide a way to write computations that can be
paused and later resumed. This module provides a way to manage the execution of
multiple such coroutines using Futures to provide concurrency. In other words,
while one coroutine is waiting for a particular Future to complete, other
coroutines can run.

Other libraries for dealing with async/await, such as asyncio in the standard
library or the third-party trio library, are focused on providing fully
asynchronous I/O capabilities. Here we focus solely on managing coroutines and
rely on Futures (themselves backed by either threads or a separate async I/O
library) to provide concurrency. This module differs from those other libraries
in two big ways: first, it is reentrant, meaning we can call `duet.run`
recursively, which makes it much easier to refactor our code incrementally to
be asynchronous; second, we can run the event loop manually one tick at a time,
which makes it possible to implement things like the pmap function below which
wraps async code into a generator interface.
"""

from __future__ import annotations

import abc
import collections
import contextlib
import functools
import inspect
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Deque,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    TypeVar,
)

import duet.impl as impl
from duet._version import __version__
from duet.aitertools import aenumerate, aiter, AnyIterable, AsyncCollector, azip
from duet.futuretools import AwaitableFuture, BufferedFuture, completed_future, failed_future

T = TypeVar("T")
U = TypeVar("U")


def run(func: Callable[..., Awaitable[T]], *args, **kwds) -> T:
    """Run an async function to completion.

    Args:
        func: The async function to run.
        *args: Positional arguments to pass to func.
        **kwds: Keyword arguments to pass to func.

    Returns:
        The final result of the async function.
    """
    with impl.Scheduler() as scheduler:
        task = scheduler.spawn(func(*args, **kwds))
    return task.result


def sync(f: Callable[..., Awaitable[T]]) -> Callable[..., T]:
    """Decorator that adds a sync version of async function or method."""
    sig = inspect.signature(f)
    first_arg = next(iter(sig.parameters), None)

    if first_arg == "self" or first_arg == "cls":
        # For class or instance methods, look up the method to call on the given
        # class or instance. This ensures that we call the right method even it
        # has been overridden in a subclass. To illustrate, consider:
        #
        #    class Parent:
        #       async def foo(self): ...
        #       foo_sync = duet.sync(foo)
        #
        #    class Child(Parent):
        #       async def foo(self): ...
        #
        # A manual implementation of foo_sync would call duet.run(self.foo) so
        # that Child().foo_sync() would call Child.foo instead of Parent.foo.
        # We want the foo_sync wrapper to work the same way. But the wrapper
        # was called with Parent.foo only, so we must look up the appropriate
        # function by name at runtime, using getattr.

        @functools.wraps(f)
        def wrapped(self_or_cls, *args, **kw):
            method = getattr(self_or_cls, f.__name__, None)
            if inspect.ismethod(method) and id(method.__func__) == wrapped_id:
                return run(f, self_or_cls, *args, **kw)
            return run(method, *args, **kw)

        wrapped_id = id(wrapped)
    else:

        @functools.wraps(f)
        def wrapped(*args, **kw):
            return run(f, *args, **kw)

    return wrapped


def awaitable(value):
    """Wraps a value to ensure that it is awaitable."""
    if inspect.isawaitable(value):
        return value
    if AwaitableFuture.isfuture(value):
        return AwaitableFuture.wrap(value)
    return _awaitable_value(value)


async def _awaitable_value(value):
    return value


def awaitable_func(function):
    """Wraps a function to ensure that it returns an awaitable."""

    if inspect.iscoroutinefunction(function):
        return function

    if inspect.isgeneratorfunction(function):
        raise TypeError(
            "cannot use generator function with duet; please convert to "
            f"async function instead: {function.__name__}"
        )

    @functools.wraps(function)
    async def wrapped(*args, **kw):
        return await awaitable(function(*args, **kw))

    return wrapped


async def pmap_async(
    func: Callable[[T], Awaitable[U]],
    iterable: AnyIterable[T],
    limit: Optional[int] = None,
) -> List[U]:
    """Apply an async function to every item in iterable.

    Args:
        func: Async function called for each element in iterable.
        iterable: Iterated over to produce values that are fed to func.
        limit: The maximum number of function calls to make concurrently.

    Returns:
        List of results of all function calls.
    """
    async with new_scope() as scope:
        return [x async for x in pmap_aiter(scope, func, iterable, limit)]


pmap = sync(pmap_async)


async def pstarmap_async(
    func: Callable[..., Awaitable[U]],
    iterable: AnyIterable[Any],
    limit: Optional[int] = None,
) -> List[U]:
    """Apply an async function to every tuple of args in iterable.

    Args:
        func: Async function called with each tuple of args in iterable.
        iterable: Iterated over to produce arg tuples that are fed to func.
        limit: The maximum number of function calls to make concurrently.

    Returns:
        List of results of all function calls.
    """
    return await pmap_async(lambda args: func(*args), iterable, limit)


pstarmap = sync(pstarmap_async)


async def pmap_aiter(
    scope: Scope,
    func: Callable[[T], Awaitable[U]],
    iterable: AnyIterable[T],
    limit: Optional[int] = None,
) -> AsyncIterator[U]:
    """Apply an async function to every item in iterable.

    Args:
        scope: Scope in which the returned async iterator must be used.
        func: Async function called for each element in iterable.
        iterable: Iterated over to produce values that are fed to func.
        limit: The maximum number of function calls to make concurrently.

    Returns:
        Asynchronous iterator that yields results in order as they become
        available.
    """
    collector = AsyncCollector[Tuple[int, U]]()

    async def task(i, arg, slot):
        try:
            value = await func(arg)
            collector.add((i, value))
        finally:
            slot.release()

    async def generate():
        try:
            limiter = Limiter(limit)
            async with new_scope() as gen_scope:
                async for i, arg in aenumerate(iterable):
                    slot = await limiter.acquire()
                    gen_scope.spawn(task, i, arg, slot)
        except Exception as e:
            collector.error(e)
        else:
            collector.done()

    scope.spawn(generate)

    buffer: Dict[int, U] = {}
    next_idx = 0
    async for i, value in collector:
        buffer[i] = value
        while next_idx in buffer:
            yield buffer.pop(next_idx)
            next_idx += 1
    while buffer:
        yield buffer.pop(next_idx)
        next_idx += 1


def pstarmap_aiter(
    scope: Scope,
    func: Callable[..., Awaitable[U]],
    iterable: AnyIterable[Any],
    limit: Optional[int] = None,
) -> AsyncIterator[U]:
    """Apply an async function to every tuple of args in iterable.

    Args:
        scope: Scope in which the returned async iterator must be used.
        func: Async function called with each tuple of args in iterable.
        iterable: Iterated over to produce arg tuples that are fed to func.
        limit: The maximum number of function calls to make concurrently.

    Returns:
        Asynchronous iterator that yields results in order as they become
        available.
    """
    return pmap_aiter(scope, lambda args: func(*args), iterable, limit)


@contextlib.asynccontextmanager
async def new_scope() -> AsyncIterator[Scope]:
    """Creates a scope in which asynchronous tasks can be launched.

    This is inspired by the concept of "nurseries" in trio:

        https://trio.readthedocs.io/en/latest/reference-core.html#nurseries-and-spawning

    We define the lifetime of a scope using an `async with` statement. Inside
    this block we can then spawn new asynchronous tasks which will run in the
    background, and the block will only exit when all spawned tasks are done.
    If an error is raised by the code in the block itself or by any of the
    spawned tasks, all other background tasks will be interrupted and the block
    will raise an error.
    """

    main_task = impl.current_task()
    scheduler = main_task.scheduler
    tasks: Set[impl.Task] = set()

    async def finish_tasks():
        while True:
            await impl.any_ready(tasks)
            tasks.intersection_update(scheduler.active_tasks)
            if not tasks:
                break

    try:
        yield Scope(main_task, scheduler, tasks)
        await finish_tasks()
    except (impl.Interrupt, Exception) as exc:
        # Interrupt remaining tasks.
        for task in tasks:
            if not task.done:
                task.interrupt(main_task, RuntimeError("scope exited"))
        # Finish remaining tasks while ignoring further interrupts.
        main_task.interruptible = False
        await finish_tasks()
        # If interrupted, raise the underlying error but suppress the context
        # (the Interrupt itself) when displaying the traceback.
        if isinstance(exc, impl.Interrupt):
            exc = exc.error
            exc.__suppress_context__ = True
        raise exc


class Scope:
    """Bounds the lifetime of async tasks spawned in the background."""

    def __init__(
        self, main_task: impl.Task, scheduler: impl.Scheduler, tasks: Set[impl.Task]
    ) -> None:
        self._main_task = main_task
        self._scheduler = scheduler
        self._tasks = tasks

    def spawn(self, func: Callable[..., Awaitable[Any]], *args, **kwds) -> None:
        """Starts a background task that will run the given function."""
        task = self._scheduler.spawn(self._run(func, *args, **kwds), main_task=self._main_task)
        self._tasks.add(task)

    async def _run(self, func: Callable[..., Awaitable[Any]], *args, **kwds) -> None:
        task = impl.current_task()
        try:
            await func(*args, **kwds)
        finally:
            self._tasks.discard(task)


class Limiter:
    """Limits concurrent access to critical resources or code blocks.

    A Limiter is created with a fixed capacity (or None to indicate no limit),
    and can then be used with async with blocks to limit access, e.g.:

        limiter = Limiter(10)
        ...
        async with limiter:
            # At most 10 async calls can be in this section at once.
            ...

    In certain situations, it may not be possible to use async with blocks to
    demarcate the critical section. In that case, one can instead call acquire
    to get a "slot" that must be released later when done using the resource:

        limiter = Limiter(10)
        ...
        slot = await limiter.acquire()
        ...
        slot.release()
    """

    def __init__(self, capacity: Optional[int]) -> None:
        self.capacity = capacity
        self.count = 0
        self.waiters: Deque[AwaitableFuture] = collections.deque()
        self.available_waiters: List[AwaitableFuture] = []

    def is_available(self) -> bool:
        """Returns True if the limiter is available, False otherwise."""
        return self.capacity is None or self.count < self.capacity

    async def __aenter__(self):
        if not self.is_available():
            f = AwaitableFuture()
            self.waiters.append(f)
            await f
        self.count += 1

    async def acquire(self) -> Slot:
        await self.__aenter__()
        return Slot(self._release)

    async def __aexit__(self, exc_type, exc, tb):
        self._release()

    def _release(self):
        self.count -= 1
        if self.waiters:
            f = self.waiters.popleft()
            f.try_set_result(None)
        if self.available_waiters:
            for f in self.available_waiters:
                f.try_set_result(None)
            self.available_waiters = []

    async def available(self) -> None:
        """Wait until this limiter is available (i.e. not full to capacity).

        Note that this always yields control to the scheduler, even if the
        limiter is currently available, to ensure that throttled iterators do
        not race ahead of downstream work.
        """
        f = AwaitableFuture()
        if self.is_available():
            f.set_result(None)
        else:
            self.available_waiters.append(f)
        await f

    async def throttle(self, iterable: AnyIterable[T]) -> AsyncIterator[T]:
        async for value in aiter(iterable):
            await self.available()
            yield value


class Slot:
    def __init__(self, release_func):
        self.release_func = release_func
        self.called = False

    def release(self):
        if self.called:
            raise Exception("Already released.")
        self.called = True
        self.release_func()


class LimitedScope(abc.ABC):
    """Combined Scope (for running async iters) and Limiter (for throttling).

    Provides convenience methods for running coroutines in parallel within this
    scope while throttling to prevent iterators from running too far ahead.
    """

    @property
    @abc.abstractmethod
    def scope(self) -> Scope:
        pass

    @property
    @abc.abstractmethod
    def limiter(self) -> Limiter:
        pass

    def spawn(self, func: Callable[..., Awaitable[Any]], *args, **kwds) -> None:
        """Starts a background task that will run the given function."""
        self.scope.spawn(func, *args, **kwds)

    async def pmap_async(
        self,
        func: Callable[[T], Awaitable[U]],
        iterable: AnyIterable[T],
    ) -> List[U]:
        return [x async for x in self.pmap_aiter(func, iterable)]

    def pmap_aiter(
        self,
        func: Callable[[T], Awaitable[U]],
        iterable: AnyIterable[T],
    ) -> AsyncIterator[U]:
        return pmap_aiter(self.scope, func, self.limiter.throttle(iterable))

    async def pstarmap_async(
        self,
        func: Callable[..., Awaitable[U]],
        iterable: AnyIterable[Any],
    ) -> List[U]:
        return [x async for x in self.pstarmap_aiter(func, iterable)]

    def pstarmap_aiter(
        self,
        func: Callable[..., Awaitable[U]],
        iterable: AnyIterable[Any],
    ) -> AsyncIterator[U]:
        return pstarmap_aiter(self.scope, func, self.limiter.throttle(iterable))
