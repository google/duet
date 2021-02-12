import asyncio
from typing import Awaitable, Callable, Optional, TypeVar

import duet.impl as impl

T = TypeVar('T')


class AsyncioRunner:
    def __init__(self, loop: Optional[asyncio.AbstractEventLoop] = None, flush_timeout: float = 1):
        self.loop = loop or asyncio.get_event_loop()
        self.flush_timeout = flush_timeout

    async def run(self, func: Callable[..., Awaitable[T]], *args, **kwds) -> T:
        result = None

        async def task():
            nonlocal result
            result = await func(*args, **kwds)

        with impl.Scheduler() as scheduler:
            scheduler.spawn(task())
            while scheduler.active_tasks:
                flush = self.loop.call_later(self.flush_timeout, scheduler.flush)
                await asyncio.wrap_future(scheduler.ready_future)
                scheduler.tick()
                flush.cancel()

        return result


async def run(func: Callable[..., Awaitable[T]], *args, **kwds) -> T:
    """Runs a duet coroutine function in asyncio."""
    return await AsyncioRunner().run(func, *args, **kwds)
